"""Orchestrator - spawns fresh air-gapped processor containers per job.

This is a TRUSTED component with:
- Docker socket access (to spawn containers)
- NO DB/S3 credentials (not needed)

For each job:
1. Creates ephemeral volume for job
2. Copies input file to job volume
3. Spawns fresh processor container (network_mode=none, read_only=true)
4. Waits for completion, destroys container
5. Copies output to shared volume for uploader
"""
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import docker
import logging
from logging.handlers import RotatingFileHandler

# Configuration
PROCESSOR_IMAGE = os.getenv("PROCESSOR_IMAGE", "thumbnail-processor:latest")
PROCESSOR_TIMEOUT = int(os.getenv("PROCESSOR_TIMEOUT", "600"))  # 10 minutes
PROCESSOR_MEMORY = os.getenv("PROCESSOR_MEMORY", "2g")
PROCESSOR_CPUS = float(os.getenv("PROCESSOR_CPUS", "2"))
PROCESSOR_RUNTIME = os.getenv("PROCESSOR_RUNTIME", "runsc")  # runsc (gVisor), kata, or runc (default Docker)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Docker volume names (must match docker-compose volume names)
# These are the actual Docker volume names, NOT paths inside this container
# Using explicit names with tte- prefix to avoid compose project name issues
INPUT_VOLUME = os.getenv("INPUT_VOLUME", "tte-queue-input")
OUTPUT_VOLUME = os.getenv("OUTPUT_VOLUME", "tte-queue-output")
STATUS_VOLUME = os.getenv("STATUS_VOLUME", "tte-queue-status")
DWG_EXCHANGE_VOLUME = os.getenv("DWG_EXCHANGE_VOLUME", "tte-dwg-exchange")

# Local paths (where volumes are mounted in THIS container)
QUEUE_DIR = Path("/queue")
INPUT_DIR = QUEUE_DIR / "input"
OUTPUT_DIR = QUEUE_DIR / "output"
STATUS_DIR = QUEUE_DIR / "status"
LOGS_DIR = Path("/app/logs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    root.handlers = []

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(LOGS_DIR / "orchestrator.log", maxBytes=10*1024*1024, backupCount=3)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return logging.getLogger("orchestrator")


logger = setup_logging()


QCAD_IMAGE = os.getenv("QCAD_IMAGE", "arjankalfsbeek/qcad:latest")
QCAD_EPHEMERAL = os.getenv("QCAD_EPHEMERAL", "true").lower() == "true"
QCAD_TIMEOUT = int(os.getenv("QCAD_TIMEOUT", "300"))


class Orchestrator:
    def __init__(self):
        self.running = True
        self.docker = docker.from_env()
        self.qcad_container = None  # For persistent mode

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        self.cleanup_qcad()

    def cleanup_qcad(self):
        """Stop persistent QCAD container if running."""
        if self.qcad_container:
            try:
                self.qcad_container.kill()
                self.qcad_container.remove(force=True)
            except Exception:
                pass
            self.qcad_container = None

    def ensure_volumes(self):
        """Ensure required Docker volumes exist."""
        for vol_name in [INPUT_VOLUME, OUTPUT_VOLUME, STATUS_VOLUME, DWG_EXCHANGE_VOLUME]:
            try:
                self.docker.volumes.get(vol_name)
            except docker.errors.NotFound:
                self.docker.volumes.create(vol_name)
                logger.info(f"Created volume: {vol_name}")

    def needs_qcad(self, filename: str) -> bool:
        """Check if file needs QCAD for conversion."""
        ext = Path(filename).suffix.lower()
        return ext in {".dwg", ".dxf"}

    def spawn_qcad_for_job(self, job_vol_name: str) -> docker.models.containers.Container | None:
        """Spawn ephemeral QCAD container for this job with gVisor/Kata isolation."""
        qcad_run_kwargs = {
            "detach": True,
            "network_mode": "none",
            "read_only": True,
            "mem_limit": "1g",
            "pids_limit": 100,
            "volumes": {
                DWG_EXCHANGE_VOLUME: {"bind": "/dwg-exchange", "mode": "rw"},
            },
            "tmpfs": {"/tmp": "size=256m,mode=1777"},
            "entrypoint": ["/bin/sh", "-c"],
            "command": """
                echo 'QCAD watcher starting...'
                while true; do
                    for f in /dwg-exchange/*.convert; do
                        [ -e "$f" ] || continue
                        job_id=$(basename "$f" .convert)
                        dwg=$(cat "$f")
                        echo "Converting: $dwg"
                        /exec/qcad/dwg2pdf -a -auto-orientation -f -o "/dwg-exchange/${job_id}.pdf" "/dwg-exchange/$dwg" 2>&1
                        if [ $? -eq 0 ] && [ -f "/dwg-exchange/${job_id}.pdf" ]; then
                            touch "/dwg-exchange/${job_id}.done"
                        else
                            echo "Conversion failed" > "/dwg-exchange/${job_id}.failed"
                        fi
                        rm -f "$f"
                    done
                    sleep 0.5
                done
            """,
        }
        
        # IMPORTANT: Use same gVisor/Kata runtime as processor for kernel isolation
        # QCAD is also untrusted (processes potentially malicious DWG files)
        if PROCESSOR_RUNTIME and PROCESSOR_RUNTIME != "runc":
            qcad_run_kwargs["runtime"] = PROCESSOR_RUNTIME
            logger.debug(f"QCAD will use runtime: {PROCESSOR_RUNTIME}")
        
        try:
            container = self.docker.containers.run(QCAD_IMAGE, **qcad_run_kwargs)
            logger.debug(f"Spawned ephemeral QCAD container: {container.short_id} (runtime={PROCESSOR_RUNTIME})")
            return container
        except Exception as e:
            logger.error(f"Failed to spawn QCAD: {e}")
            return None

    def process_job(self, content_hash: str) -> bool:
        """Process a single job in a fresh container."""
        input_file = INPUT_DIR / f"{content_hash}.bin"
        meta_file = INPUT_DIR / f"{content_hash}.json"
        ready_file = STATUS_DIR / f"{content_hash}.ready"

        if not input_file.exists() or not meta_file.exists():
            logger.error(f"Input files missing for {content_hash[:8]}")
            ready_file.unlink(missing_ok=True)
            return False

        meta = json.loads(meta_file.read_text())
        job_vol_name = f"job-{content_hash[:12]}"
        qcad_container = None

        try:
            # Create ephemeral job volume
            job_vol = self.docker.volumes.create(job_vol_name)
            logger.debug(f"Created volume: {job_vol_name}")

            # Copy input to job volume using temporary container
            # NOTE: Use Docker volume names, not paths (spawned container can't see our filesystem)
            self.docker.containers.run(
                "alpine",
                command=f"sh -c 'cp /in/{content_hash}.bin /work/input.bin && cp /in/{content_hash}.json /work/job.json'",
                volumes={
                    INPUT_VOLUME: {"bind": "/in", "mode": "ro"},
                    job_vol_name: {"bind": "/work", "mode": "rw"},
                },
                remove=True,
                network_mode="none",
            )

            # Spawn ephemeral QCAD if needed for this file type
            filename = meta.get("original_filename", "")
            if QCAD_EPHEMERAL and self.needs_qcad(filename):
                qcad_container = self.spawn_qcad_for_job(job_vol_name)
                if not qcad_container:
                    logger.warning(f"QCAD spawn failed for {content_hash[:8]}, DWG conversion may fail")

            # Run processor in fresh air-gapped container with gVisor/Kata isolation
            run_kwargs = {
                "detach": True,
                "network_mode": "none",
                "read_only": True,
                "mem_limit": PROCESSOR_MEMORY,
                "nano_cpus": int(PROCESSOR_CPUS * 1e9),
                "pids_limit": 200,
                "volumes": {
                    job_vol_name: {"bind": "/work", "mode": "rw"},
                    DWG_EXCHANGE_VOLUME: {"bind": "/dwg-exchange", "mode": "rw"},
                },
                "tmpfs": {"/tmp": "size=512m,mode=1777"},
            }
            
            # Use gVisor/Kata runtime for kernel-level isolation
            if PROCESSOR_RUNTIME and PROCESSOR_RUNTIME != "runc":
                run_kwargs["runtime"] = PROCESSOR_RUNTIME
            
            container = self.docker.containers.run(PROCESSOR_IMAGE, **run_kwargs)

            logger.info(f"Processing {meta.get('original_filename', content_hash[:8])} in container {container.short_id}")

            # Wait for completion with timeout
            try:
                result = container.wait(timeout=PROCESSOR_TIMEOUT)
                exit_code = result.get("StatusCode", -1)
                
                # Get container logs
                container_logs = container.logs().decode("utf-8", errors="replace")
                logger.debug(f"Container {container.short_id} logs:\n{container_logs}")
                
            except Exception as e:
                logger.error(f"Container timeout/error for {content_hash[:8]}: {e}")
                container.kill()
                exit_code = -1

            # Always remove processor container (fresh per job)
            container.remove(force=True)
            
            # Kill ephemeral QCAD container
            if qcad_container:
                try:
                    qcad_container.kill()
                    qcad_container.remove(force=True)
                    logger.debug(f"Removed ephemeral QCAD container")
                except Exception:
                    pass

            if exit_code != 0:
                logger.warning(f"Processor exited with code {exit_code} for {content_hash[:8]}")

            # Copy results from job volume to output
            self.docker.containers.run(
                "alpine",
                command="sh -c 'cp -r /work/* /out/ 2>/dev/null || true'",
                volumes={
                    job_vol_name: {"bind": "/work", "mode": "ro"},
                    OUTPUT_VOLUME: {"bind": "/out", "mode": "rw"},
                },
                remove=True,
                network_mode="none",
            )

            # Rename outputs with content_hash prefix
            work_result = OUTPUT_DIR / "result.json"
            work_thumb = OUTPUT_DIR / "thumbnail.png"
            work_log = OUTPUT_DIR / "processor.log"

            if work_result.exists():
                work_result.rename(OUTPUT_DIR / f"{content_hash}.result.json")
            if work_thumb.exists():
                work_thumb.rename(OUTPUT_DIR / f"{content_hash}.thumbnail.png")
            if work_log.exists():
                work_log.rename(OUTPUT_DIR / f"{content_hash}.log")

            # Signal done for uploader
            done_file = STATUS_DIR / f"{content_hash}.done"
            done_file.write_text(json.dumps(meta))

            return True

        except Exception as e:
            logger.error(f"Failed to process {content_hash[:8]}: {e}", exc_info=True)
            # Signal failed for uploader
            failed_file = STATUS_DIR / f"{content_hash}.failed"
            failed_file.write_text(str(e))
            return False

        finally:
            # Cleanup: remove input files, job volume, and ephemeral QCAD
            ready_file.unlink(missing_ok=True)
            input_file.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            if qcad_container:
                try:
                    qcad_container.kill()
                    qcad_container.remove(force=True)
                except Exception:
                    pass
            try:
                self.docker.volumes.get(job_vol_name).remove(force=True)
            except Exception:
                pass
            # Clean up any leftover files in dwg-exchange for this job
            try:
                self.docker.containers.run(
                    "alpine",
                    command=f"sh -c 'rm -f /dwg-exchange/{content_hash[:12]}*'",
                    volumes={DWG_EXCHANGE_VOLUME: {"bind": "/dwg-exchange", "mode": "rw"}},
                    remove=True,
                    network_mode="none",
                )
            except Exception:
                pass

    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        logger.info("Orchestrator starting")
        logger.info(f"Processor image: {PROCESSOR_IMAGE}")
        logger.info(f"Processor runtime: {PROCESSOR_RUNTIME}")
        logger.info(f"Processor timeout: {PROCESSOR_TIMEOUT}s")
        logger.info(f"Processor limits: {PROCESSOR_MEMORY} RAM, {PROCESSOR_CPUS} CPUs")

        self.ensure_volumes()

        while self.running:
            # Find ready jobs
            ready_files = list(STATUS_DIR.glob("*.ready"))

            if ready_files:
                for ready_file in ready_files:
                    if not self.running:
                        break
                    content_hash = ready_file.stem
                    self.process_job(content_hash)
            else:
                time.sleep(1)

        logger.info("Orchestrator stopped")


def main():
    orchestrator = Orchestrator()
    orchestrator.run()


if __name__ == "__main__":
    main()

