"""File processing: thumbnail generation and text extraction."""
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
import fitz  # PyMuPDF
from pdf2image import convert_from_path

from src import settings
from src.logging_conf import logger


def get_file_extension(filename: str) -> str:
    """Get lowercase file extension."""
    return Path(filename).suffix.lower()


def is_image(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_IMAGE_EXTENSIONS


def is_pdf(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_PDF_EXTENSIONS


def is_dwg(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_DWG_EXTENSIONS


def is_text_file(filename: str) -> bool:
    return get_file_extension(filename) in settings.TEXT_EXTRACT_EXTENSIONS


def can_generate_thumbnail(filename: str) -> bool:
    return is_image(filename) or is_pdf(filename) or is_dwg(filename)


def can_extract_text(filename: str) -> bool:
    return is_pdf(filename) or is_text_file(filename)


def create_cover_thumbnail(img: Image.Image, width: int, height: int) -> Image.Image:
    """Create thumbnail with cover crop (center-crop to fill dimensions)."""
    target_ratio = width / height
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        # Image is wider - crop sides
        new_width = int(img.height * target_ratio)
        left = (img.width - new_width) // 2
        img = img.crop((left, 0, left + new_width, img.height))
    else:
        # Image is taller - crop top/bottom
        new_height = int(img.width / target_ratio)
        top = (img.height - new_height) // 2
        img = img.crop((0, top, img.width, top + new_height))

    return img.resize((width, height), Image.Resampling.LANCZOS)


def convert_dwg_to_image(source_path: Path, temp_dir: Path) -> Optional[Path]:
    """Convert DWG/DXF to PNG using LibreDWG."""
    job_id = uuid.uuid4()
    
    try:
        # Method 1: Try dwgbmp to extract embedded thumbnail
        bmp_path = temp_dir / f"dwg_{job_id}.bmp"
        result = subprocess.run(
            ["dwgbmp", str(source_path), str(bmp_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0 and bmp_path.exists() and bmp_path.stat().st_size > 0:
            logger.info(f"DWG embedded thumbnail extracted: {source_path.name}")
            return bmp_path
        
        # Cleanup failed attempt
        bmp_path.unlink(missing_ok=True)
        
        # Method 2: Convert to SVG, then to PNG
        svg_path = temp_dir / f"dwg_{job_id}.svg"
        png_path = temp_dir / f"dwg_{job_id}.png"
        
        with open(svg_path, 'w') as svg_file:
            result = subprocess.run(
                ["dwg2SVG", str(source_path)],
                stdout=svg_file,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60
            )
        
        if result.returncode != 0 or not svg_path.exists():
            logger.warning(f"dwg2SVG failed for {source_path.name}: {result.stderr[:200] if result.stderr else 'no output'}")
            return None
        
        # Convert SVG to PNG using rsvg-convert (better quality than ImageMagick for SVG)
        result = subprocess.run(
            ["rsvg-convert", "-w", "800", "-h", "600", "-o", str(png_path), str(svg_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Cleanup SVG
        svg_path.unlink(missing_ok=True)
        
        if result.returncode == 0 and png_path.exists():
            logger.info(f"DWG converted via SVG: {source_path.name}")
            return png_path
        
        logger.warning(f"SVG to PNG conversion failed for {source_path.name}")
        return None
        
    except subprocess.TimeoutExpired:
        logger.error(f"DWG conversion timed out for {source_path.name}")
        return None
    except FileNotFoundError as e:
        logger.error(f"LibreDWG tools not found: {e}")
        return None
    except Exception as e:
        logger.error(f"DWG conversion failed for {source_path.name}: {e}", exc_info=True)
        return None


def generate_thumbnail(source_path: Path, dest_path: Path, temp_dir: Optional[Path] = None) -> bool:
    """Generate thumbnail for image, PDF, or DWG."""
    try:
        ext = get_file_extension(source_path.name)
        width, height = settings.THUMBNAIL_WIDTH, settings.THUMBNAIL_HEIGHT

        if ext in settings.THUMBNAIL_DWG_EXTENSIONS:
            # DWG/DXF: Convert to image using LibreDWG
            if temp_dir is None:
                temp_dir = settings.TEMP_DIR
            img_path = convert_dwg_to_image(source_path, temp_dir)
            if not img_path:
                return False
            img = Image.open(img_path)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # Cleanup temp image
            img_path.unlink(missing_ok=True)
        elif ext in settings.THUMBNAIL_PDF_EXTENSIONS:
            # PDF: Convert first page to image
            images = convert_from_path(str(source_path), first_page=1, last_page=1, dpi=150)
            if not images:
                return False
            img = images[0]
        else:
            # Image file
            img = Image.open(source_path)
            # Convert to RGB if necessary (handles RGBA, P, etc.)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

        thumbnail = create_cover_thumbnail(img, width, height)
        thumbnail.save(dest_path, "PNG", optimize=True)
        return True

    except Exception as e:
        logger.error(f"Failed to generate thumbnail for {source_path.name}: {e}")
        return False


def extract_text_from_pdf(source_path: Path) -> Optional[str]:
    """Extract selectable text from PDF (no OCR)."""
    try:
        doc = fitz.open(source_path)
        text_parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                text_parts.append(text)
        doc.close()
        
        full_text = "\n\n".join(text_parts)
        if len(full_text) > settings.MAX_TEXT_LENGTH:
            full_text = full_text[:settings.MAX_TEXT_LENGTH]
        return full_text if full_text.strip() else None

    except Exception as e:
        logger.error(f"Failed to extract PDF text from {source_path.name}: {e}")
        return None


def extract_text_from_file(source_path: Path) -> Optional[str]:
    """Extract text from plain text file."""
    try:
        # Try UTF-8 first, fall back to latin-1
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                text = f.read(settings.MAX_TEXT_LENGTH)
        except UnicodeDecodeError:
            with open(source_path, "r", encoding="latin-1") as f:
                text = f.read(settings.MAX_TEXT_LENGTH)
        return text if text.strip() else None

    except Exception as e:
        logger.error(f"Failed to extract text from {source_path.name}: {e}")
        return None


def extract_text(source_path: Path) -> Optional[str]:
    """Extract text from file (PDF or plain text)."""
    if is_pdf(source_path.name):
        return extract_text_from_pdf(source_path)
    elif is_text_file(source_path.name):
        return extract_text_from_file(source_path)
    return None


def process_file(source_path: Path, temp_dir: Path) -> Tuple[Optional[Path], Optional[str]]:
    """
    Process a file: generate thumbnail and extract text.
    Returns (thumbnail_path, extracted_text).
    """
    thumbnail_path = None
    extracted_text = None

    # Generate thumbnail
    if can_generate_thumbnail(source_path.name):
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if generate_thumbnail(source_path, thumb_path, temp_dir):
            thumbnail_path = thumb_path

    # Extract text
    if can_extract_text(source_path.name):
        extracted_text = extract_text(source_path)

    return thumbnail_path, extracted_text

