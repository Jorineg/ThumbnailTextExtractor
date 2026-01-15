# ThumbnailTextExtractor - Secure Document Processing Service

An automated background worker that generates visual previews and extracts searchable text from files uploaded to the IBHelm ecosystem, using a **defense-in-depth air-gapped architecture**.

## 🔒 Security Architecture

The service uses a multi-container architecture designed to contain potential exploits from malicious files:

```
┌─────────────────────────────────────────────────────────────┐
│  FETCHER (trusted)                                          │
│  - Has S3/DB credentials                                    │
│  - Has network access                                       │
│  - Downloads ONE file at a time to queue volume             │
└─────────────────┬───────────────────────────────────────────┘
                  │ /queue/input (one file at a time)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR (trusted, no credentials)                     │
│  - Has docker.sock access                                   │
│  - Spawns FRESH processor container per job                 │
│  - Destroys container + volume after job                    │
└─────────────────┬───────────────────────────────────────────┘
                  │          
          ┌───────┴───────────┐
          │  PROCESSOR        │  ← Fresh container per job
          │  runtime: gVisor  │  ← Kernel-level isolation
          │  network: none    │  ← NO network at all
          │  read_only: true  │  ← Cannot modify code
          │  memory: 2g       │  ← Resource limits
          └───────┬───────────┘
                  │ Also communicates with QCAD via volume
                  │
┌─────────────────┴───────────────────────────────────────────┐
│  QCAD (untrusted, air-gapped)                               │
│  - runtime: gVisor      ← Same kernel isolation as processor│
│  - network: none                                            │
│  - Fresh per DWG job (QCAD_EPHEMERAL=true)                  │
│  - File-based IPC for DWG/DXF conversion                    │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼ /queue/output
┌─────────────────────────────────────────────────────────────┐
│  UPLOADER (trusted)                                         │
│  - Re-encodes thumbnails (destroys steganography)           │
│  - Sanitizes extracted text                                 │
│  - Forwards processor logs to BetterStack                   │
│  - Has S3/DB credentials                                    │
└─────────────────────────────────────────────────────────────┘
```

### Security Properties

| Threat | Mitigation |
|--------|------------|
| Malicious file exploits processor | gVisor/Kata kernel isolation + air-gapped (no network) |
| Kernel-level exploit | gVisor implements its own syscalls; Kata uses hardware VMs |
| Code injection persists | Fresh container per job, destroyed after |
| Credential theft | Processor/QCAD have NO credentials |
| Data exfiltration via covert channel | Thumbnails re-encoded (destroys steganography) |
| DWG library exploits | QCAD also gVisor-isolated, ephemeral, air-gapped |

**Worst case with full code execution**: Attacker would need to escape gVisor/Kata sandbox (very rare), still can't access network/credentials, cannot persist, can only DoS.

## 🛠 Capabilities

### 🖼 Visual Processing
- **Standard Images**: JPG, PNG, WebP, GIF, BMP, TIFF
- **Modern Formats**: Native HEIC/HEIF support
- **CAD/Engineering**: DWG and DXF via air-gapped QCAD sidecar
- **Office**: Excel, Word, PowerPoint (including .xls/.doc) via LibreOffice
- **SVG**: Vector graphics via CairoSVG
- **Video**: Frame extraction via ffmpeg
- **Intelligent Cropping**: Top-crop logic for engineering documents

### 🔍 Deep Inspection
- **Archive Extraction**: Embedded thumbnails from .pages, .numbers, .key, .sketch, .afdesign
- **OLE Documents**: BMP previews from Nova/Trimble .n4d, .n4m files

### 📝 Text Extraction
- **PDF Text**: High-fidelity extraction via PyMuPDF
- **Code & Data**: .json, .xml, .yaml, .sql, .md, source files
- **Smart Fallback**: Unknown formats (IFC, etc.) if printable char ratio passes

## 🚀 Deployment

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SUPABASE_URL` | Supabase project URL | Required |
| `SUPABASE_SERVICE_KEY` | Service role key | Required |
| `STORAGE_BUCKET` | Source bucket for files | `files` |
| `THUMBNAIL_BUCKET` | Destination bucket | `thumbnails` |
| `POLL_INTERVAL` | Seconds between queue checks | `5` |
| `MAX_RETRIES` | Max retry attempts | `3` |
| `PROCESSOR_TIMEOUT` | Max processing time per job | `600` |
| `PROCESSOR_MEMORY` | Memory limit for processor | `2g` |
| `PROCESSOR_CPUS` | CPU limit for processor | `2` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `BETTERSTACK_SOURCE_TOKEN` | BetterStack logging token | Optional |

### Run Secure Architecture

```bash
# Build and start
docker compose -f docker-compose.secure.yml up -d --build

# View logs
docker compose -f docker-compose.secure.yml logs -f

# Check individual components
docker logs tte-fetcher
docker logs tte-orchestrator
docker logs tte-uploader
docker logs tte-qcad
```

### Build Processor Image Only

```bash
docker build -f Dockerfile.processor -t tte-processor:latest .
```

## 🔄 Workflow

1. **Fetch**: Fetcher claims pending `file_contents` record, downloads file to queue volume
2. **Orchestrate**: Orchestrator creates fresh ephemeral volume, copies input, spawns air-gapped processor
3. **Process**: Processor generates thumbnail and/or extracts text, writes to job volume
4. **Cleanup**: Processor container destroyed, job volume removed
5. **Upload**: Uploader sanitizes outputs (re-encodes thumbnails), uploads to S3, updates DB
6. **Log**: Processor logs forwarded to BetterStack via uploader

## 🏗 Architecture Files

| File | Purpose |
|------|---------|
| `Dockerfile.processor` | Air-gapped processor image (heavy, all processing tools) |
| `Dockerfile.trusted` | Trusted components image (lightweight, network tools) |
| `docker-compose.secure.yml` | Full secure architecture |
| `docker-compose.yml` | Legacy single-container (not recommended for production) |
| `src/fetcher.py` | Downloads files, claims jobs |
| `src/orchestrator.py` | Spawns fresh containers per job |
| `src/uploader.py` | Sanitizes and uploads results |
| `src/process_job.py` | Air-gapped job wrapper |
| `src/processor.py` | Core processing logic |
| `src/qcad_watcher.sh` | File-based IPC for QCAD |

## 🔐 Runtime Isolation Notes

The processor container runs with:
- `network_mode: none` - No IP stack, no DNS, no sockets
- `read_only: true` - Cannot modify filesystem
- `mem_limit` / `nano_cpus` - Resource constraints
- `pids_limit` - Prevent fork bombs
- `tmpfs` for `/tmp` - Writable scratch space

For maximum isolation, consider running with:
- **gVisor**: `docker run --runtime=runsc ...`
- **Kata Containers**: `docker run --runtime=kata ...`
