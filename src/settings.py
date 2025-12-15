"""Configuration for ThumbnailTextExtractor."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = DATA_DIR / "temp"

LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BETTERSTACK_SOURCE_TOKEN = os.getenv("BETTERSTACK_SOURCE_TOKEN")
BETTERSTACK_INGEST_HOST = os.getenv("BETTERSTACK_INGEST_HOST")

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Storage
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "files")
THUMBNAIL_BUCKET = os.getenv("THUMBNAIL_BUCKET", "thumbnails")

# Processing
THUMBNAIL_WIDTH = int(os.getenv("THUMBNAIL_WIDTH", "400"))
THUMBNAIL_HEIGHT = int(os.getenv("THUMBNAIL_HEIGHT", "300"))
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "51200"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# Supported formats
THUMBNAIL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
THUMBNAIL_PDF_EXTENSIONS = {".pdf"}
THUMBNAIL_DWG_EXTENSIONS = {".dwg", ".dxf"}
TEXT_EXTRACT_EXTENSIONS = {".txt", ".json", ".xml", ".js", ".ts", ".css", ".html", ".md", ".csv", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".log", ".sql", ".py", ".sh", ".bash"}

# ODA File Converter path (AppImage in /usr/local/bin)
ODA_CONVERTER_PATH = os.getenv("ODA_CONVERTER_PATH", "/usr/local/bin/ODAFileConverter")


def validate_config():
    """Validate required configuration."""
    errors = []
    if not SUPABASE_URL:
        errors.append("SUPABASE_URL is required")
    if not SUPABASE_SERVICE_KEY:
        errors.append("SUPABASE_SERVICE_KEY is required")
    if errors:
        raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))

