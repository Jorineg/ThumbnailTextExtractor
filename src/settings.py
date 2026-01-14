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
THUMBNAIL_LARGE_WIDTH = int(os.getenv("THUMBNAIL_LARGE_WIDTH", "800"))
THUMBNAIL_LARGE_HEIGHT = int(os.getenv("THUMBNAIL_LARGE_HEIGHT", "600"))
# File extensions that use the smaller thumbnail size (comma-separated, e.g. "pdf,png,jpg,jpeg,heic,heif,gif")
THUMBNAIL_SMALL_EXTENSIONS_RAW = os.getenv("THUMBNAIL_SMALL_EXTENSIONS", "pdf,png,jpg,jpeg,heic,heif,gif")
THUMBNAIL_SMALL_EXTENSIONS = {f".{ext.strip().lower()}" for ext in THUMBNAIL_SMALL_EXTENSIONS_RAW.split(",") if ext.strip()}
THUMBNAIL_CROP_POSITION = os.getenv("THUMBNAIL_CROP_POSITION", "top")  # "top" or "center"
# DWG processing: high-res intermediate for content-aware cropping
DWG_INTERMEDIATE_DPI = int(os.getenv("DWG_INTERMEDIATE_DPI", "600"))
DWG_WHITE_THRESHOLD = int(os.getenv("DWG_WHITE_THRESHOLD", "250"))  # Pixel value above which is considered "white"
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "51200"))
TEXT_FALLBACK_MAX_SIZE = int(os.getenv("TEXT_FALLBACK_MAX_SIZE", "204800"))  # 200KB max for unknown text files
TEXT_FALLBACK_MIN_PRINTABLE = float(os.getenv("TEXT_FALLBACK_MIN_PRINTABLE", "0.99"))  # 99% printable chars required
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# Supported formats
THUMBNAIL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".heif"}
THUMBNAIL_PDF_EXTENSIONS = {".pdf"}
THUMBNAIL_DWG_EXTENSIONS = {".dwg", ".dxf"}  # Converted via QCAD sidecar
THUMBNAIL_OFFICE_EXTENSIONS = {".xlsx", ".xls", ".ods", ".docx", ".doc", ".odt", ".pptx", ".ppt", ".odp", ".pages", ".numbers", ".key"}  # Via LibreOffice
TEXT_EXTRACT_EXTENSIONS = {".txt", ".json", ".xml", ".js", ".ts", ".css", ".html", ".md", ".csv", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".log", ".py", ".sh", ".bash"}

# Common thumbnail paths in zip-based document formats
ARCHIVE_THUMBNAIL_PATHS = [
    "Thumbnails/Preview.jpg",
    "Thumbnails/Preview.png",
    "QuickLook/Thumbnail.jpg",
    "QuickLook/Thumbnail.png",
    "QuickLook/Preview.jpg",
    "QuickLook/Preview.png",
    "preview.png",
    "preview.jpg",
    "previews/preview.png",
    "previews/preview.jpg",
]


def validate_config():
    """Validate required configuration."""
    errors = []
    if not SUPABASE_URL:
        errors.append("SUPABASE_URL is required")
    if not SUPABASE_SERVICE_KEY:
        errors.append("SUPABASE_SERVICE_KEY is required")
    if errors:
        raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))

