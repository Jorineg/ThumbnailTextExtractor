"""Fetcher - trusted component that downloads files from S3 and claims jobs.

This is a TRUSTED component with:
- Network access (to reach Supabase/S3)
- DB credentials (to claim jobs)

It downloads files to a shared volume for the orchestrator to pick up.
"""
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from logtail import LogtailHandler
import logging
from logging.handlers import RotatingFileHandler

# Configuration
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "files")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BETTERSTACK_TOKEN = os.getenv("BETTERSTACK_SOURCE_TOKEN")
BETTERSTACK_HOST = os.getenv("BETTERSTACK_INGEST_HOST")

QUEUE_DIR = Path("/queue")
INPUT_DIR = QUEUE_DIR / "input"
STATUS_DIR = QUEUE_DIR / "status"
LOGS_DIR = Path("/app/logs")

INPUT_DIR.mkdir(parents=True, exist_ok=True)
STATUS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    root.handlers = []

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(LOGS_DIR / "fetcher.log", maxBytes=10*1024*1024, backupCount=3)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if BETTERSTACK_TOKEN:
        try:
            kwargs = {"source_token": BETTERSTACK_TOKEN}
            if BETTERSTACK_HOST:
                kwargs["host"] = BETTERSTACK_HOST
            bs_handler = LogtailHandler(**kwargs)
            bs_handler.setFormatter(fmt)
            root.addHandler(bs_handler)
        except Exception as e:
            root.warning(f"Failed to init BetterStack: {e}")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return logging.getLogger("fetcher")


logger = setup_logging()

headers = {
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "apikey": SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


class Fetcher:
    def __init__(self):
        self.running = True
        self.client = httpx.Client(timeout=60.0)

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def claim_job(self) -> dict | None:
        """Claim one pending job from DB."""
        try:
            url = f"{SUPABASE_URL}/rest/v1/rpc/claim_pending_file_content"
            response = self.client.post(url, headers=headers, json={"p_limit": 1})
            response.raise_for_status()
            jobs = response.json()
            return jobs[0] if jobs else None
        except Exception as e:
            logger.error(f"Failed to claim job: {e}")
            return None

    def download_file(self, job: dict) -> bool:
        """Download file to input volume."""
        content_hash = job["content_hash"]
        storage_path = job["storage_path"]
        full_path = job.get("full_path", storage_path)

        if not storage_path:
            logger.warning(f"Job {content_hash[:8]} has no storage_path")
            return False

        filename = Path(full_path).name if full_path else Path(storage_path).name
        extension = Path(filename).suffix.lower()

        try:
            url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{storage_path}"
            with self.client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                file_path = INPUT_DIR / f"{content_hash}.bin"
                with open(file_path, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)

            # Write job metadata
            meta = {
                "content_hash": content_hash,
                "storage_path": storage_path,
                "original_filename": filename,
                "original_extension": extension,
                "try_count": job.get("try_count", 0),
            }
            meta_path = INPUT_DIR / f"{content_hash}.json"
            meta_path.write_text(json.dumps(meta))

            # Signal job ready for orchestrator
            ready_path = STATUS_DIR / f"{content_hash}.ready"
            ready_path.touch()

            logger.info(f"Fetched: {filename} ({content_hash[:8]})")
            return True

        except Exception as e:
            logger.error(f"Failed to download {content_hash[:8]}: {e}")
            self.mark_failed(content_hash, job.get("try_count", 0) + 1)
            return False

    def mark_failed(self, content_hash: str, try_count: int):
        """Mark job as failed in DB."""
        try:
            url = f"{SUPABASE_URL}/rest/v1/file_contents"
            params = {"content_hash": f"eq.{content_hash}"}
            now = datetime.now(timezone.utc).isoformat()
            max_retries = int(os.getenv("MAX_RETRIES", "3"))
            status = "error" if try_count >= max_retries else "pending"
            data = {
                "processing_status": status,
                "try_count": try_count,
                "last_status_change": now,
                "db_updated_at": now,
            }
            self.client.patch(url, headers=headers, params=params, json=data)
        except Exception as e:
            logger.error(f"Failed to mark {content_hash[:8]} as failed: {e}")

    def check_pending_jobs(self) -> int:
        """Check how many jobs are waiting in input dir."""
        return len(list(STATUS_DIR.glob("*.ready")))

    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        logger.info("Fetcher starting")
        logger.info(f"Supabase: {SUPABASE_URL}")
        logger.info(f"Storage bucket: {STORAGE_BUCKET}")
        logger.info(f"Poll interval: {POLL_INTERVAL}s")

        while self.running:
            # Don't fetch if queue is getting too large (backpressure)
            pending = self.check_pending_jobs()
            if pending >= 10:
                logger.debug(f"Queue has {pending} pending jobs, waiting...")
                time.sleep(POLL_INTERVAL)
                continue

            job = self.claim_job()
            if job:
                self.download_file(job)
            else:
                # No jobs available, wait
                for _ in range(POLL_INTERVAL):
                    if not self.running:
                        break
                    time.sleep(1)

        self.client.close()
        logger.info("Fetcher stopped")


def main():
    fetcher = Fetcher()
    fetcher.run()


if __name__ == "__main__":
    main()

