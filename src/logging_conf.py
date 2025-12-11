"""Logging configuration."""
import logging
import sys
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime, timezone
from logtail import LogtailHandler

from src import settings


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "file_id"):
            log_data["file_id"] = record.file_id
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    root_logger.handlers = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, settings.LOG_LEVEL))
    console_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler
    log_file = settings.LOGS_DIR / "app.log"
    file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)

    # BetterStack handler
    if settings.BETTERSTACK_SOURCE_TOKEN:
        try:
            handler_kwargs = {"source_token": settings.BETTERSTACK_SOURCE_TOKEN}
            if settings.BETTERSTACK_INGEST_HOST:
                handler_kwargs["host"] = settings.BETTERSTACK_INGEST_HOST
            betterstack_handler = LogtailHandler(**handler_kwargs)
            betterstack_handler.setLevel(logging.DEBUG)
            betterstack_handler.setFormatter(console_formatter)
            root_logger.addHandler(betterstack_handler)
            root_logger.info("BetterStack logging enabled")
        except Exception as e:
            root_logger.warning(f"Failed to initialize BetterStack logging: {e}")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return root_logger


logger = setup_logging()

