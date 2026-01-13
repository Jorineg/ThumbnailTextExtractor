"""Queue operations via Supabase REST API using file_contents.processing_status."""
import httpx
from typing import List, Dict, Any
from datetime import datetime, timezone

from src import settings
from src.logging_conf import logger


class QueueClient:
    """REST client for file_contents processing queue."""

    def __init__(self):
        self.base_url = f"{settings.SUPABASE_URL}/rest/v1"
        self.headers = {
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_KEY,
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._client = httpx.Client(timeout=30.0)

    def fetch_pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch pending items from file_contents where s3_status=uploaded and processing_status=pending."""
        try:
            url = f"{self.base_url}/file_contents"
            params = {
                "select": "content_hash,storage_path,size_bytes,try_count",
                "s3_status": "eq.uploaded",
                "processing_status": "eq.pending",
                "order": "db_created_at.asc",
                "limit": str(limit),
            }
            response = self._client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            items = response.json()
            
            # For each item, get a representative full_path from files table for extension detection
            for item in items:
                file_url = f"{self.base_url}/files"
                file_params = {
                    "select": "full_path",
                    "content_hash": f"eq.{item['content_hash']}",
                    "limit": "1"
                }
                file_resp = self._client.get(file_url, headers=self.headers, params=file_params)
                if file_resp.status_code == 200 and file_resp.json():
                    item["full_path"] = file_resp.json()[0]["full_path"]
                else:
                    item["full_path"] = None
            
            return items
        except Exception as e:
            logger.error(f"Failed to fetch pending queue items: {e}")
            return []

    def mark_processing(self, content_hash: str) -> bool:
        """Mark item as processing."""
        try:
            url = f"{self.base_url}/file_contents"
            params = {"content_hash": f"eq.{content_hash}"}
            data = {
                "processing_status": "processing",
                "last_status_change": datetime.now(timezone.utc).isoformat(),
                "db_updated_at": datetime.now(timezone.utc).isoformat()
            }
            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to mark {content_hash[:8]} as processing: {e}")
            return False

    def mark_completed(self, content_hash: str, thumbnail_path: str | None, extracted_text: str | None) -> bool:
        """Mark item as done and store results."""
        try:
            url = f"{self.base_url}/file_contents"
            params = {"content_hash": f"eq.{content_hash}"}
            now = datetime.now(timezone.utc).isoformat()
            data = {
                "processing_status": "done",
                "last_status_change": now,
                "db_updated_at": now
            }
            if thumbnail_path is not None:
                data["thumbnail_path"] = thumbnail_path
                data["thumbnail_generated_at"] = now
            if extracted_text is not None:
                data["extracted_text"] = extracted_text
            
            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to mark {content_hash[:8]} as completed: {e}")
            return False

    def mark_failed(self, content_hash: str, try_count: int) -> bool:
        """Mark item as error or back to pending for retry."""
        try:
            url = f"{self.base_url}/file_contents"
            params = {"content_hash": f"eq.{content_hash}"}
            now = datetime.now(timezone.utc).isoformat()
            
            status = "error" if try_count >= settings.MAX_RETRIES else "pending"
            data = {
                "processing_status": status,
                "try_count": try_count,
                "last_status_change": now,
                "db_updated_at": now
            }
            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to mark {content_hash[:8]} as failed: {e}")
            return False

    def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics for monitoring."""
        try:
            stats = {"pending": 0, "processing": 0, "done": 0, "error": 0}
            url = f"{self.base_url}/file_contents"
            
            for status in stats.keys():
                params = {"processing_status": f"eq.{status}", "s3_status": "eq.uploaded", "select": "content_hash"}
                headers = {**self.headers, "Prefer": "count=exact"}
                response = self._client.head(url, headers=headers, params=params)
                count = response.headers.get("content-range", "0").split("/")[-1]
                stats[status] = int(count) if count != "*" else 0
            
            return stats
        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {"pending": 0, "processing": 0, "done": 0, "error": 0}

    def close(self):
        self._client.close()
