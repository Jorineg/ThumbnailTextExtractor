"""Queue operations via Supabase REST API using file_contents.processing_status."""
import httpx
from datetime import datetime, timezone
from typing import Any

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

    def claim_pending(self, limit: int = 5) -> list[dict[str, Any]]:
        """Atomically claim pending items (SELECT FOR UPDATE SKIP LOCKED + mark as indexing)."""
        try:
            url = f"{self.base_url}/rpc/claim_pending_file_content"
            response = self._client.post(url, headers=self.headers, json={"p_limit": limit})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to claim pending queue items: {e}")
            return []

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

    def get_queue_stats(self) -> dict[str, int]:
        """Get queue statistics for monitoring."""
        try:
            stats = {"pending": 0, "indexing": 0, "done": 0, "error": 0}
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
            return {"pending": 0, "indexing": 0, "done": 0, "error": 0}

    def close(self):
        self._client.close()
