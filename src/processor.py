"""File processing: thumbnail generation and text extraction."""
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
import pillow_heif  # Registers HEIC/HEIF support with Pillow
import fitz  # PyMuPDF
from pdf2image import convert_from_path

pillow_heif.register_heif_opener()  # Enable HEIC support in Pillow

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


def convert_dwg_to_pdf(source_path: Path) -> Optional[Path]:
    """Convert DWG/DXF to PDF using QCAD sidecar container."""
    job_id = uuid.uuid4()
    exchange_dir = Path("/dwg-exchange")
    qcad_container = os.getenv("QCAD_CONTAINER", "qcad")
    
    try:
        # Copy DWG to exchange volume
        dwg_name = f"{job_id}{source_path.suffix}"
        exchange_dwg = exchange_dir / dwg_name
        pdf_name = f"{job_id}.pdf"
        exchange_pdf = exchange_dir / pdf_name
        
        shutil.copy2(source_path, exchange_dwg)
        
        # Convert via QCAD container: dwg2pdf with auto-fit and auto-orientation
        result = subprocess.run(
            [
                "docker", "exec", qcad_container,
                "/exec/qcad/dwg2pdf",
                "-a",  # auto-fit
                "-auto-orientation",
                "-f",  # force overwrite
                "-o", f"/dwg-exchange/{pdf_name}",
                f"/dwg-exchange/{dwg_name}"
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        # Cleanup input
        exchange_dwg.unlink(missing_ok=True)
        
        if result.returncode == 0 and exchange_pdf.exists():
            logger.info(f"DWG converted to PDF: {source_path.name}")
            return exchange_pdf
        
        logger.warning(f"QCAD dwg2pdf failed for {source_path.name}: {result.stderr[:500] if result.stderr else 'no output'}")
        return None
        
    except subprocess.TimeoutExpired:
        logger.error(f"DWG conversion timed out for {source_path.name}")
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
            # DWG/DXF: Convert to PDF via QCAD, then to image
            pdf_path = convert_dwg_to_pdf(source_path)
            if not pdf_path:
                return False
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=150)
            # Cleanup temp PDF
            pdf_path.unlink(missing_ok=True)
            if not images:
                return False
            img = images[0]
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
    """Extract text from file (PDF or plain text). DWG handled in process_file."""
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

    # Special handling for DWG: convert once, use for both thumbnail and text
    if is_dwg(source_path.name):
        pdf_path = convert_dwg_to_pdf(source_path)
        if pdf_path:
            # Generate thumbnail from PDF
            thumb_name = f"{uuid.uuid4()}.png"
            thumb_path = temp_dir / thumb_name
            if generate_thumbnail(pdf_path, thumb_path, temp_dir):
                thumbnail_path = thumb_path
            # Extract text from PDF
            extracted_text = extract_text_from_pdf(pdf_path)
            # Cleanup
            pdf_path.unlink(missing_ok=True)
        return thumbnail_path, extracted_text

    # Standard processing for other file types
    if can_generate_thumbnail(source_path.name):
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if generate_thumbnail(source_path, thumb_path, temp_dir):
            thumbnail_path = thumb_path

    if can_extract_text(source_path.name):
        extracted_text = extract_text(source_path)

    return thumbnail_path, extracted_text

