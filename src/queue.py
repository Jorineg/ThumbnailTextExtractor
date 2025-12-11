"""Queue operations via Supabase REST API."""
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from src import settings
from src.logging_conf import logger


class QueueClient:
    """REST client for thumbnail processing queue."""

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
        """Fetch pending items from queue, joined with file info."""
        try:
            url = f"{self.base_url}/thumbnail_processing_queue"
            params = {
                "select": "id,file_id,attempts,files(id,storage_path,filename)",
                "status": "eq.pending",
                "order": "created_at.asc",
                "limit": str(limit),
            }
            response = self._client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch pending queue items: {e}")
            return []

    def mark_processing(self, queue_id: int) -> bool:
        """Mark item as processing."""
        try:
            url = f"{self.base_url}/thumbnail_processing_queue"
            params = {"id": f"eq.{queue_id}"}
            data = {"status": "processing", "updated_at": datetime.now(timezone.utc).isoformat()}
            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to mark queue item {queue_id} as processing: {e}")
            return False

    def mark_completed(self, queue_id: int) -> bool:
        """Mark item as completed."""
        try:
            url = f"{self.base_url}/thumbnail_processing_queue"
            params = {"id": f"eq.{queue_id}"}
            now = datetime.now(timezone.utc).isoformat()
            data = {"status": "completed", "processed_at": now, "updated_at": now}
            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to mark queue item {queue_id} as completed: {e}")
            return False

    def mark_failed(self, queue_id: int, error: str, attempts: int) -> bool:
        """Mark item as failed or pending (for retry)."""
        try:
            url = f"{self.base_url}/thumbnail_processing_queue"
            params = {"id": f"eq.{queue_id}"}
            now = datetime.now(timezone.utc).isoformat()
            
            if attempts >= settings.MAX_RETRIES:
                status = "failed"
            else:
                status = "pending"  # Will be retried

            data = {
                "status": status,
                "attempts": attempts,
                "last_error": error[:500],
                "updated_at": now,
            }
            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to mark queue item {queue_id} as failed: {e}")
            return False

    def update_file_results(self, file_id: str, thumbnail_path: Optional[str], extracted_text: Optional[str]) -> bool:
        """Update file record with thumbnail path and extracted text."""
        try:
            url = f"{self.base_url}/files"
            params = {"id": f"eq.{file_id}"}
            now = datetime.now(timezone.utc).isoformat()
            
            data = {"db_updated_at": now}
            if thumbnail_path is not None:
                data["thumbnail_path"] = thumbnail_path
                data["thumbnail_generated_at"] = now
            if extracted_text is not None:
                data["extracted_text"] = extracted_text

            response = self._client.patch(url, headers=self.headers, params=params, json=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to update file {file_id}: {e}")
            return False

    def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics for monitoring."""
        try:
            stats = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
            url = f"{self.base_url}/thumbnail_processing_queue"
            
            for status in stats.keys():
                params = {"status": f"eq.{status}", "select": "id"}
                headers = {**self.headers, "Prefer": "count=exact"}
                response = self._client.head(url, headers=headers, params=params)
                count = response.headers.get("content-range", "0").split("/")[-1]
                stats[status] = int(count) if count != "*" else 0
            
            return stats
        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {"pending": 0, "processing": 0, "completed": 0, "failed": 0}

    def close(self):
        self._client.close()

