"""Main application: poll queue and process files."""
import sys
import time
import signal
import os
from pathlib import Path

from src import settings
from src.logging_conf import logger
from src.queue import QueueClient
from src.storage import StorageClient
from src.processor import process_file, can_generate_thumbnail, can_extract_text


class App:
    def __init__(self):
        self.running = True
        self.queue = QueueClient()
        self.storage = StorageClient()

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def process_queue_item(self, item: dict) -> bool:
        """Process a single queue item."""
        queue_id = item["id"]
        file_info = item.get("files")
        
        if not file_info:
            logger.warning(f"Queue item {queue_id} has no file info, marking failed")
            self.queue.mark_failed(queue_id, "No file info", item["attempts"] + 1)
            return False

        file_id = file_info["id"]
        storage_path = file_info["storage_path"]
        filename = file_info["filename"]

        # Skip if file type not supported for any processing
        if not can_generate_thumbnail(filename) and not can_extract_text(filename):
            logger.debug(f"Skipping unsupported file type: {filename}")
            self.queue.mark_completed(queue_id)
            return True

        logger.info(f"Processing: {filename}", extra={"file_id": file_id})

        # Download file to temp
        temp_file = settings.TEMP_DIR / f"{file_id}_{filename}"
        if not self.storage.download_file(settings.STORAGE_BUCKET, storage_path, temp_file):
            self.queue.mark_failed(queue_id, "Download failed", item["attempts"] + 1)
            return False

        try:
            # Process file
            thumbnail_local, extracted_text = process_file(temp_file, settings.TEMP_DIR)

            # Upload thumbnail if generated
            thumbnail_storage_path = None
            if thumbnail_local and thumbnail_local.exists():
                thumbnail_storage_path = f"{file_id}.png"
                if not self.storage.upload_file(
                    settings.THUMBNAIL_BUCKET, thumbnail_storage_path, thumbnail_local, "image/png"
                ):
                    thumbnail_storage_path = None
                    logger.warning(f"Failed to upload thumbnail for {filename}")
                # Clean up local thumbnail
                thumbnail_local.unlink(missing_ok=True)

            # Update file record
            if not self.queue.update_file_results(file_id, thumbnail_storage_path, extracted_text):
                self.queue.mark_failed(queue_id, "DB update failed", item["attempts"] + 1)
                return False

            self.queue.mark_completed(queue_id)
            
            result_parts = []
            if thumbnail_storage_path:
                result_parts.append("thumbnail")
            if extracted_text:
                result_parts.append(f"text ({len(extracted_text)} chars)")
            
            logger.info(f"Completed: {filename} - {', '.join(result_parts) if result_parts else 'no output'}")
            return True

        except Exception as e:
            logger.error(f"Error processing {filename}: {e}", exc_info=True)
            self.queue.mark_failed(queue_id, str(e), item["attempts"] + 1)
            return False

        finally:
            # Clean up temp file
            temp_file.unlink(missing_ok=True)

    def run(self):
        """Main run loop."""
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        try:
            settings.validate_config()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)

        # Ensure thumbnail bucket exists
        if not self.storage.ensure_bucket_exists(settings.THUMBNAIL_BUCKET):
            logger.error("Failed to ensure thumbnail bucket exists")
            sys.exit(1)

        logger.info("ThumbnailTextExtractor starting")
        logger.info(f"Storage bucket: {settings.STORAGE_BUCKET}")
        logger.info(f"Thumbnail bucket: {settings.THUMBNAIL_BUCKET}")
        logger.info(f"Thumbnail size: {settings.THUMBNAIL_WIDTH}x{settings.THUMBNAIL_HEIGHT}")
        logger.info(f"Poll interval: {settings.POLL_INTERVAL}s")

        processed_count = 0
        
        try:
            while self.running:
                # Check storage availability before processing
                if not self.storage.is_available():
                    logger.warning("Storage unavailable, waiting...")
                    time.sleep(settings.POLL_INTERVAL)
                    continue

                # Fetch pending items
                items = self.queue.fetch_pending(limit=5)

                if items:
                    for item in items:
                        if not self.running:
                            break
                        
                        queue_id = item["id"]
                        if not self.queue.mark_processing(queue_id):
                            continue
                        
                        if self.process_queue_item(item):
                            processed_count += 1

                    # Log stats periodically
                    if processed_count > 0 and processed_count % 10 == 0:
                        stats = self.queue.get_queue_stats()
                        logger.info(f"Queue stats: {stats}")

                else:
                    # No items, wait before polling again
                    for _ in range(settings.POLL_INTERVAL):
                        if not self.running:
                            break
                        time.sleep(1)

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            self.queue.close()
            self.storage.close()
            logger.info(f"ThumbnailTextExtractor stopped. Processed {processed_count} files.")


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()

