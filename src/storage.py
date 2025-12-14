"""Supabase Storage REST client."""
import httpx
from pathlib import Path
from typing import Optional

from src import settings
from src.logging_conf import logger


class StorageClient:
    """REST client for Supabase Storage."""

    def __init__(self):
        self.base_url = f"{settings.SUPABASE_URL}/storage/v1"
        self.headers = {
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_KEY,
        }
        self._client = httpx.Client(timeout=60.0)

    def download_file(self, bucket: str, path: str, dest: Path) -> bool:
        """Download file from storage to local path."""
        try:
            url = f"{self.base_url}/object/{bucket}/{path}"
            with self._client.stream("GET", url, headers=self.headers) as response:
                response.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"Failed to download {bucket}/{path}: {e}")
            return False

    def upload_file(self, bucket: str, path: str, local_path: Path, content_type: str) -> bool:
        """Upload file to storage."""
        try:
            url = f"{self.base_url}/object/{bucket}/{path}"
            with open(local_path, "rb") as f:
                headers = {**self.headers, "Content-Type": content_type}
                response = self._client.post(url, content=f.read(), headers=headers)
                if response.status_code == 400 and "already exists" in response.text.lower():
                    # File exists, try upsert
                    response = self._client.put(url, content=open(local_path, "rb").read(), headers=headers)
                response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to upload to {bucket}/{path}: {e}")
            return False

    def delete_file(self, bucket: str, path: str) -> bool:
        """Delete file from storage."""
        try:
            url = f"{self.base_url}/object/{bucket}"
            response = self._client.delete(url, headers=self.headers, json={"prefixes": [path]})
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Failed to delete {bucket}/{path}: {e}")
            return False

    def is_available(self) -> bool:
        """Check if storage service is reachable."""
        try:
            url = f"{self.base_url}/bucket/{settings.STORAGE_BUCKET}"
            response = self._client.get(url, headers=self.headers, timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    def ensure_bucket_exists(self, bucket: str) -> bool:
        """Ensure bucket exists, create if not."""
        try:
            url = f"{self.base_url}/bucket/{bucket}"
            response = self._client.get(url, headers=self.headers)
            if response.status_code == 404:
                create_url = f"{self.base_url}/bucket"
                response = self._client.post(
                    create_url,
                    headers={**self.headers, "Content-Type": "application/json"},
                    json={"id": bucket, "name": bucket, "public": False}
                )
                response.raise_for_status()
                logger.info(f"Created bucket: {bucket}")
            return True
        except Exception as e:
            logger.error(f"Failed to ensure bucket {bucket}: {e}")
            return False

    def close(self):
        self._client.close()

