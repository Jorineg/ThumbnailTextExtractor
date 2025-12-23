# ThumbnailTextExtractor - Document Processing Service

An automated background worker that generates visual previews and extracts searchable text from files uploaded to the IBHelm ecosystem.

## 🛠 Capabilities

The extractor is more than a simple thumbnail generator. It uses a suite of specialized tools to handle complex engineering and office formats:

### 🖼 Advanced Visual Processing
- **Standard Images**: Full support for JPG, PNG, WebP, GIF, BMP, and TIFF.
- **Modern Formats**: Native **HEIC/HEIF** support for mobile photos.
- **CAD/Engineering**: Converts **DWG and DXF** files to high-quality previews using a QCAD sidecar container.
- **Office Integration**: Seamlessly handles **Excel, Word, and PowerPoint** (including older .xls/.doc formats) via headless LibreOffice conversion.
- **Intelligent Cropping**: Uses "Top-Crop" logic by default—ideal for architectural plans and engineering documents where the title block or most relevant info is at the top.

### 🔍 Deep Inspection (Metadata & Archive Extraction)
- **Archive Extraction**: Inspects zip-based formats (.pages, .numbers, .key, .sketch, .afdesign, .idraw) to extract embedded thumbnails without full decompression.
- **OLE Documents**: Reaches into OLE compound documents (e.g., Nova/Trimble .n4d, .n4m) to retrieve embedded BMP preview streams.

### 📝 Intelligent Text Extraction
- **PDF Text**: High-fidelity extraction of selectable text from PDF documents.
- **Code & Data**: Native support for .json, .xml, .yaml, .sql, .md, and various source code files.
- **Smart Fallback**: Attempts to extract text from unknown formats (like **IFC** or specialized logging formats) if they satisfy printable character ratio and binary safety checks.
- **Storage Optimized**: Cleans text (removes null bytes) and truncates content to safely fit within PostgreSQL text limits while remaining fully searchable.

## ⚙️ Tech Stack

- **Python 3.9+**
- **PyMuPDF / pdf2image**: For PDF rendering and text extraction.
- **Pillow (PIL)**: For image processing and thumbnail resizing.
- **Supabase-py**: For cloud storage and database interaction.

## 🚀 Deployment

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Your Supabase project URL. |
| `SUPABASE_SERVICE_KEY` | Service role key for storage and DB access. |
| `STORAGE_BUCKET` | Source bucket for original files (e.g., `files`). |
| `THUMBNAIL_BUCKET` | Destination bucket for thumbnails (e.g., `thumbnails`). |
| `POLL_INTERVAL` | Seconds between queue checks (default: 5). |

### Run with Docker

```bash
docker compose up -d
```

## 🔄 Workflow

1. **Trigger**: A new file record is inserted into `public.files`, and a database trigger adds a task to `thumbnail_processing_queue`.
2. **Claim**: The extractor claims a 'pending' task and sets its status to 'processing'.
3. **Execution**:
   - Downloads the file.
   - Generates a 200x200 (default) PNG thumbnail.
   - Extracts plain text if the file is a PDF.
4. **Completion**:
   - Uploads the thumbnail.
   - Updates the `files` table with the `thumbnail_path` and `extracted_text`.
   - Marks the queue task as 'completed'.

## 🔒 Security

Uses the Supabase `service_role` key to bypass RLS for processing tasks. Ensure this service runs in a secure, internal network environment.
