"""Minimal settings for air-gapped processor - NO network configuration."""
import os

# Processing directories (mounted volumes)
WORK_DIR = "/work"
DWG_EXCHANGE_DIR = "/dwg-exchange"

# Thumbnail dimensions
THUMBNAIL_WIDTH = int(os.getenv("THUMBNAIL_WIDTH", "400"))
THUMBNAIL_HEIGHT = int(os.getenv("THUMBNAIL_HEIGHT", "300"))
THUMBNAIL_LARGE_WIDTH = int(os.getenv("THUMBNAIL_LARGE_WIDTH", "800"))
THUMBNAIL_LARGE_HEIGHT = int(os.getenv("THUMBNAIL_LARGE_HEIGHT", "600"))
THUMBNAIL_SMALL_EXTENSIONS_RAW = os.getenv("THUMBNAIL_SMALL_EXTENSIONS", "pdf,png,jpg,jpeg,heic,heif,gif,svg")
THUMBNAIL_SMALL_EXTENSIONS = {f".{ext.strip().lower()}" for ext in THUMBNAIL_SMALL_EXTENSIONS_RAW.split(",") if ext.strip()}
THUMBNAIL_CROP_POSITION = os.getenv("THUMBNAIL_CROP_POSITION", "top")

# DWG processing
DWG_INTERMEDIATE_DPI = int(os.getenv("DWG_INTERMEDIATE_DPI", "600"))
DWG_WHITE_THRESHOLD = int(os.getenv("DWG_WHITE_THRESHOLD", "250"))

# Text extraction limits
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "51200"))
TEXT_FALLBACK_MAX_SIZE = int(os.getenv("TEXT_FALLBACK_MAX_SIZE", "204800"))
TEXT_FALLBACK_MIN_PRINTABLE = float(os.getenv("TEXT_FALLBACK_MIN_PRINTABLE", "0.99"))

# Supported formats
THUMBNAIL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".heif"}
THUMBNAIL_PDF_EXTENSIONS = {".pdf"}
THUMBNAIL_DWG_EXTENSIONS = {".dwg", ".dxf"}
THUMBNAIL_SVG_EXTENSIONS = {".svg"}
THUMBNAIL_VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".webm", ".mkv", ".m4v"}
THUMBNAIL_OFFICE_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".ods", ".docx", ".doc", ".docm", ".odt", ".pptx", ".ppt", ".pptm", ".odp", ".pages", ".numbers", ".key"}
TEXT_EXTRACT_EXTENSIONS = {".txt", ".json", ".xml", ".js", ".ts", ".css", ".html", ".md", ".csv", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".log", ".py", ".sh", ".bash"}

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

