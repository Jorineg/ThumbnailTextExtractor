"""Fetcher - trusted component that downloads files from S3 and claims jobs.

This is a TRUSTED component with:
- Network access (to reach S3)
- Minimal DB credentials (can ONLY call claim_pending_file_content)

Security: Uses direct PostgreSQL with tte_fetcher role instead of service_role key.
The tte_fetcher role can ONLY execute claim_pending_file_content - nothing else.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

import httpx
import psycopg
from logtail import LogtailHandler
import logging
from logging.handlers import RotatingFileHandler

# Configuration
# DB: Minimal role that can ONLY claim pending files
DB_DSN = os.environ["TTE_FETCHER_DB_DSN"]  # postgresql://tte_fetcher:xxx@host:5432/db

# S3: For downloading files (could also use pre-signed URLs for even more security)
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # TODO: Replace with minimal S3 credentials
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "files")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
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

# S3 headers (still using service key for S3 - TODO: use minimal S3 credentials)
s3_headers = {
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "apikey": SUPABASE_SERVICE_KEY,
}


class Fetcher:
    def __init__(self):
        self.running = True
        self.http_client = httpx.Client(timeout=60.0)
        self.db_conn = None

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def connect_db(self):
        """Connect to PostgreSQL with minimal tte_fetcher role."""
        if self.db_conn is None or self.db_conn.closed:
            self.db_conn = psycopg.connect(DB_DSN)
            logger.info("Connected to PostgreSQL as tte_fetcher")
        return self.db_conn

    def claim_job(self) -> dict | None:
        """Claim one pending job from DB using minimal role.
        
        The tte_fetcher role can ONLY execute this function - nothing else.
        """
        try:
            conn = self.connect_db()
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM claim_pending_file_content(1)")
                row = cur.fetchone()
            conn.commit()  # Release transaction lock - prevents blocking uploader UPDATEs
            if row:
                # Function returns: content_hash, storage_path, size_bytes, try_count, full_path
                return {
                    "content_hash": row[0],
                    "storage_path": row[1],
                    "size_bytes": row[2],
                    "try_count": row[3],
                    "full_path": row[4],
                }
            return None
        except Exception as e:
            logger.error(f"Failed to claim job: {e}")
            if self.db_conn:
                self.db_conn.rollback()
            self.db_conn = None  # Force reconnect
            return None

    def download_file(self, job: dict) -> bool:
        """Download file from S3 to input volume."""
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
            with self.http_client.stream("GET", url, headers=s3_headers) as response:
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
            # Note: We can't mark failed in DB - tte_fetcher has no UPDATE permission
            # The job will remain in 'indexing' status and eventually timeout/retry
            return False

    def check_pending_jobs(self) -> int:
        """Check how many jobs are waiting in input dir."""
        return len(list(STATUS_DIR.glob("*.ready")))

    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        logger.info("Fetcher starting (minimal DB role: tte_fetcher)")
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

        if self.db_conn:
            self.db_conn.close()
        self.http_client.close()
        logger.info("Fetcher stopped")


def main():
    fetcher = Fetcher()
    fetcher.run()


if __name__ == "__main__":
    main()
