"""Uploader - trusted component that sanitizes and uploads results.

This is a TRUSTED component with:
- Network access (to reach S3)
- Minimal DB credentials (can ONLY update specific columns on file_contents)

Security: Uses direct PostgreSQL with tte_uploader role instead of service_role key.
The tte_uploader role can ONLY UPDATE specific columns - nothing else.
"""
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from PIL import Image
from logtail import LogtailHandler
import logging
from logging.handlers import RotatingFileHandler

# Configuration
# DB: Minimal role that can ONLY update file_contents results
DB_DSN = os.environ["TTE_UPLOADER_DB_DSN"]  # postgresql://tte_uploader:xxx@host:5432/db

# S3: For uploading thumbnails (could also use pre-signed URLs for even more security)
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # TODO: Replace with minimal S3 credentials
THUMBNAIL_BUCKET = os.getenv("THUMBNAIL_BUCKET", "thumbnails")

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BETTERSTACK_TOKEN = os.getenv("BETTERSTACK_SOURCE_TOKEN")
BETTERSTACK_HOST = os.getenv("BETTERSTACK_INGEST_HOST")

# Sanitization limits
MAX_THUMBNAIL_SIZE = 1_000_000  # 1MB
MAX_TEXT_LENGTH = 51200
ALLOWED_THUMBNAIL_DIMS = [(400, 300), (800, 600), (1000, 750)]  # Allow configured sizes

QUEUE_DIR = Path("/queue")
OUTPUT_DIR = QUEUE_DIR / "output"
STATUS_DIR = QUEUE_DIR / "status"
LOGS_DIR = Path("/app/logs")

LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    root.handlers = []

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(LOGS_DIR / "uploader.log", maxBytes=10*1024*1024, backupCount=3)
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
    return logging.getLogger("uploader")


logger = setup_logging()
processor_logger = logging.getLogger("processor")  # For forwarding processor logs

# S3 headers
s3_headers = {
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "apikey": SUPABASE_SERVICE_KEY,
}


class Uploader:
    def __init__(self):
        self.running = True
        self.http_client = httpx.Client(timeout=60.0)
        self.db_conn = None

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def connect_db(self):
        """Connect to PostgreSQL with minimal tte_uploader role."""
        if self.db_conn is None or self.db_conn.closed:
            self.db_conn = psycopg.connect(DB_DSN)
            logger.info("Connected to PostgreSQL as tte_uploader")
        return self.db_conn

    def forward_processor_logs(self, log_file: Path, content_hash: str):
        """Forward processor logs to our logging system."""
        if not log_file.exists():
            return
        
        try:
            for line in log_file.read_text().strip().split("\n"):
                if line.strip():
                    processor_logger.info(f"[{content_hash[:8]}] {line}")
        except Exception as e:
            logger.warning(f"Failed to forward processor logs: {e}")
        finally:
            log_file.unlink(missing_ok=True)

    def sanitize_thumbnail(self, input_path: Path, output_path: Path) -> bool:
        """Re-encode thumbnail to destroy any hidden data (steganography)."""
        try:
            img = Image.open(input_path)
            
            # Validate dimensions (warn but allow - config may vary)
            if img.size not in ALLOWED_THUMBNAIL_DIMS:
                logger.debug(f"Non-standard thumbnail dimensions: {img.size}")
            
            # Validate file size
            if input_path.stat().st_size > MAX_THUMBNAIL_SIZE:
                logger.warning(f"Thumbnail too large: {input_path.stat().st_size} bytes")
                return False
            
            # Force RGB mode, strip all metadata, re-encode
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            # Create new image from pixels (destroys LSB steganography)
            clean = Image.new("RGB", img.size, (255, 255, 255))
            clean.paste(img)
            
            # Save with optimization (further destroys steganography)
            clean.save(output_path, "PNG", optimize=True)
            
            logger.debug(f"Sanitized thumbnail: {input_path.stat().st_size} -> {output_path.stat().st_size} bytes")
            return True
            
        except Exception as e:
            logger.error(f"Failed to sanitize thumbnail: {e}")
            return False

    def sanitize_text(self, text: str) -> str:
        """Sanitize extracted text."""
        if not text:
            return text
        
        # Truncate
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        
        # Remove null bytes
        text = text.replace('\x00', '')
        
        # Remove non-printable chars (except whitespace)
        text = re.sub(r'[^\x20-\x7E\n\r\t\u00A0-\uFFFF]', '', text)
        
        return text

    def upload_thumbnail(self, local_path: Path, storage_path: str) -> bool:
        """Upload thumbnail to S3."""
        try:
            url = f"{SUPABASE_URL}/storage/v1/object/{THUMBNAIL_BUCKET}/{storage_path}"
            with open(local_path, "rb") as f:
                data = f.read()
            
            upload_headers = {**s3_headers, "Content-Type": "image/png"}
            response = self.http_client.post(url, content=data, headers=upload_headers)
            
            if response.status_code == 400 and "already exists" in response.text.lower():
                response = self.http_client.put(url, content=data, headers=upload_headers)
            
            response.raise_for_status()
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload thumbnail: {e}")
            return False

    def update_db_success(self, content_hash: str, thumbnail_path: str | None, extracted_text: str | None) -> bool:
        """Update file_contents record with success.
        
        The tte_uploader role can ONLY update these specific columns.
        """
        try:
            conn = self.connect_db()
            now = datetime.now(timezone.utc)
            
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE file_contents SET
                        processing_status = 'done',
                        thumbnail_path = COALESCE(%s, thumbnail_path),
                        thumbnail_generated_at = CASE WHEN %s IS NOT NULL THEN %s ELSE thumbnail_generated_at END,
                        extracted_text = COALESCE(%s, extracted_text),
                        last_status_change = %s,
                        db_updated_at = %s
                    WHERE content_hash = %s
                """, (
                    thumbnail_path,
                    thumbnail_path, now,
                    extracted_text,
                    now, now,
                    content_hash
                ))
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to update DB for {content_hash[:8]}: {e}")
            if self.db_conn:
                self.db_conn.rollback()
            self.db_conn = None
            return False

    def update_db_failed(self, content_hash: str, try_count: int) -> bool:
        """Mark job as failed in DB.
        
        The tte_uploader role can ONLY update these specific columns.
        """
        try:
            conn = self.connect_db()
            now = datetime.now(timezone.utc)
            status = "error" if try_count >= MAX_RETRIES else "pending"
            
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE file_contents SET
                        processing_status = %s,
                        try_count = %s,
                        last_status_change = %s,
                        db_updated_at = %s
                    WHERE content_hash = %s
                """, (status, try_count, now, now, content_hash))
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to mark {content_hash[:8]} as failed: {e}")
            if self.db_conn:
                self.db_conn.rollback()
            self.db_conn = None
            return False

    def process_done(self, content_hash: str, meta: dict):
        """Process a completed job."""
        result_file = OUTPUT_DIR / f"{content_hash}.result.json"
        thumb_file = OUTPUT_DIR / f"{content_hash}.thumbnail.png"
        log_file = OUTPUT_DIR / f"{content_hash}.log"
        
        # Forward processor logs
        self.forward_processor_logs(log_file, content_hash)
        
        if not result_file.exists():
            logger.error(f"No result.json for {content_hash[:8]}")
            self.update_db_failed(content_hash, meta.get("try_count", 0) + 1)
            return
        
        result = json.loads(result_file.read_text())
        
        if not result.get("success"):
            logger.warning(f"Processing failed for {content_hash[:8]}: {result.get('error')}")
            self.update_db_failed(content_hash, meta.get("try_count", 0) + 1)
            self.cleanup_output(content_hash)
            return
        
        thumbnail_storage_path = None
        extracted_text = None
        
        # Sanitize and upload thumbnail
        if result.get("thumbnail_file") and thumb_file.exists():
            sanitized_path = OUTPUT_DIR / f"{content_hash}.sanitized.png"
            if self.sanitize_thumbnail(thumb_file, sanitized_path):
                thumbnail_storage_path = f"{content_hash}.png"
                if self.upload_thumbnail(sanitized_path, thumbnail_storage_path):
                    logger.info(f"Uploaded thumbnail for {content_hash[:8]}")
                else:
                    thumbnail_storage_path = None
                sanitized_path.unlink(missing_ok=True)
        
        # Sanitize text
        if result.get("extracted_text"):
            extracted_text = self.sanitize_text(result["extracted_text"])
        
        # Update DB
        if self.update_db_success(content_hash, thumbnail_storage_path, extracted_text):
            parts = []
            if thumbnail_storage_path:
                parts.append("thumbnail")
            if extracted_text:
                parts.append(f"text ({len(extracted_text)} chars)")
            logger.info(f"Completed: {meta.get('original_filename', content_hash[:8])} - {', '.join(parts) if parts else 'no output'}")
        else:
            self.update_db_failed(content_hash, meta.get("try_count", 0) + 1)
        
        self.cleanup_output(content_hash)

    def process_failed(self, content_hash: str, error: str, meta: dict):
        """Process a failed job."""
        logger.error(f"Job failed for {content_hash[:8]}: {error}")
        self.update_db_failed(content_hash, meta.get("try_count", 0) + 1)
        self.cleanup_output(content_hash)

    def cleanup_output(self, content_hash: str):
        """Clean up output files."""
        for pattern in [f"{content_hash}.*"]:
            for f in OUTPUT_DIR.glob(pattern):
                f.unlink(missing_ok=True)

    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        logger.info("Uploader starting (minimal DB role: tte_uploader)")
        logger.info(f"Thumbnail bucket: {THUMBNAIL_BUCKET}")

        while self.running:
            # Process done jobs
            for done_file in STATUS_DIR.glob("*.done"):
                if not self.running:
                    break
                content_hash = done_file.stem
                meta = json.loads(done_file.read_text())
                done_file.unlink()
                self.process_done(content_hash, meta)

            # Process failed jobs
            for failed_file in STATUS_DIR.glob("*.failed"):
                if not self.running:
                    break
                content_hash = failed_file.stem
                error = failed_file.read_text()
                failed_file.unlink()
                # Try to load meta from input dir (might not exist)
                meta_file = QUEUE_DIR / "input" / f"{content_hash}.json"
                meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
                self.process_failed(content_hash, error, meta)

            time.sleep(1)

        if self.db_conn:
            self.db_conn.close()
        self.http_client.close()
        logger.info("Uploader stopped")


def main():
    uploader = Uploader()
    uploader.run()


if __name__ == "__main__":
    main()
