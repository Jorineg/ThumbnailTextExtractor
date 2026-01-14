"""File processing: thumbnail generation and text extraction."""
import os
import shutil
import subprocess
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image
import pillow_heif  # Registers HEIC/HEIF support with Pillow
import fitz  # PyMuPDF
import olefile
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


def is_office(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_OFFICE_EXTENSIONS


def is_text_file(filename: str) -> bool:
    return get_file_extension(filename) in settings.TEXT_EXTRACT_EXTENSIONS


def can_generate_thumbnail(filename: str) -> bool:
    return is_image(filename) or is_pdf(filename) or is_dwg(filename) or is_office(filename)


def get_thumbnail_dimensions(filename: str) -> Tuple[int, int]:
    """Get thumbnail dimensions based on file type."""
    ext = get_file_extension(filename)
    if ext in settings.THUMBNAIL_SMALL_EXTENSIONS:
        return settings.THUMBNAIL_WIDTH, settings.THUMBNAIL_HEIGHT
    return settings.THUMBNAIL_LARGE_WIDTH, settings.THUMBNAIL_LARGE_HEIGHT


def can_extract_text(filename: str) -> bool:
    return is_pdf(filename) or is_text_file(filename)


def create_cover_thumbnail(img: Image.Image, width: int, height: int) -> Image.Image:
    """Create thumbnail with cover crop."""
    target_ratio = width / height
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        # Image is wider - crop sides (always center)
        new_width = int(img.height * target_ratio)
        left = (img.width - new_width) // 2
        img = img.crop((left, 0, left + new_width, img.height))
    else:
        # Image is taller - crop based on THUMBNAIL_CROP_POSITION
        new_height = int(img.width / target_ratio)
        top = 0 if settings.THUMBNAIL_CROP_POSITION == "top" else (img.height - new_height) // 2
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
            timeout=300  # 5 minutes for large/complex DWG files
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


def convert_office_to_pdf(source_path: Path, temp_dir: Path) -> Optional[Path]:
    """Convert Office docs (xlsx, docx, pptx, etc.) to PDF using LibreOffice."""
    try:
        result = subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(temp_dir),
                str(source_path)
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            logger.warning(f"LibreOffice conversion failed for {source_path.name}: {result.stderr[:500] if result.stderr else 'no output'}")
            return None
        
        pdf_path = temp_dir / f"{source_path.stem}.pdf"
        if pdf_path.exists():
            logger.info(f"Office doc converted to PDF: {source_path.name}")
            return pdf_path
        
        logger.warning(f"LibreOffice output PDF not found for {source_path.name}")
        return None
        
    except subprocess.TimeoutExpired:
        logger.error(f"Office conversion timed out for {source_path.name}")
        return None
    except Exception as e:
        logger.error(f"Office conversion failed for {source_path.name}: {e}", exc_info=True)
        return None


def find_content_bounds(img: Image.Image, threshold: int = 250) -> Tuple[int, int, int, int]:
    """
    Find bounding box of non-white content in image.
    Returns (left, top, right, bottom) coordinates.
    """
    # Convert to grayscale for analysis
    gray = img.convert("L")
    arr = np.array(gray)
    
    # Find non-white pixels (below threshold)
    non_white = arr < threshold
    
    if not non_white.any():
        # All white, return full image
        return 0, 0, img.width, img.height
    
    # Find bounds
    rows = np.any(non_white, axis=1)
    cols = np.any(non_white, axis=0)
    
    top = np.argmax(rows)
    bottom = len(rows) - np.argmax(rows[::-1])
    left = np.argmax(cols)
    right = len(cols) - np.argmax(cols[::-1])
    
    return left, top, right, bottom


def crop_to_content(img: Image.Image, margin_ratio: float = 0.02) -> Image.Image:
    """
    Crop image to content bounds with a small margin.
    Used for DWG files where content may be in a corner of the page.
    """
    left, top, right, bottom = find_content_bounds(img, settings.DWG_WHITE_THRESHOLD)
    
    content_width = right - left
    content_height = bottom - top
    
    if content_width <= 0 or content_height <= 0:
        return img
    
    # Add margin (percentage of content dimensions)
    margin_x = int(content_width * margin_ratio)
    margin_y = int(content_height * margin_ratio)
    
    # Expand bounds with margin, clamped to image bounds
    left = max(0, left - margin_x)
    top = max(0, top - margin_y)
    right = min(img.width, right + margin_x)
    bottom = min(img.height, bottom + margin_y)
    
    cropped = img.crop((left, top, right, bottom))
    logger.debug(f"Content crop: {img.width}x{img.height} -> {cropped.width}x{cropped.height}")
    return cropped


def extract_archive_thumbnail(source_path: Path, dest_path: Path, width: int, height: int) -> bool:
    """
    Extract embedded thumbnail from zip-based document formats.
    Works with .idraw, .sketch, .graffle, .pages, .numbers, .key, .afdesign, etc.
    """
    try:
        if not zipfile.is_zipfile(source_path):
            logger.debug(f"Not a valid zip archive: {source_path.name}")
            return False

        with zipfile.ZipFile(source_path, 'r') as zf:
            names = zf.namelist()
            for thumb_path in settings.ARCHIVE_THUMBNAIL_PATHS:
                if thumb_path in names:
                    data = zf.read(thumb_path)
                    img = Image.open(BytesIO(data))
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    thumbnail = create_cover_thumbnail(img, width, height)
                    thumbnail.save(dest_path, "PNG", optimize=True)
                    logger.info(f"Extracted thumbnail from {source_path.name} ({thumb_path})")
                    return True

        logger.debug(f"No thumbnail found in archive: {source_path.name}")
        return False

    except Exception as e:
        logger.error(f"Failed to extract archive thumbnail from {source_path.name}: {e}")
        return False


def extract_ole_thumbnail(source_path: Path, dest_path: Path, width: int, height: int) -> bool:
    """
    Extract thumbnail from OLE compound documents.
    Works with Nova/Trimble files (.n4d, .n4m, .nbup, .nbum) that store BMP in BITMAP stream.
    """
    try:
        if not olefile.isOleFile(source_path):
            return False

        ole = olefile.OleFileIO(source_path)
        try:
            if ole.exists('BITMAP'):
                bmp_data = ole.openstream('BITMAP').read()
                if bmp_data[:2] == b'BM':  # Valid BMP header
                    img = Image.open(BytesIO(bmp_data))
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    thumbnail = create_cover_thumbnail(img, width, height)
                    thumbnail.save(dest_path, "PNG", optimize=True)
                    logger.info(f"Extracted OLE thumbnail from {source_path.name}")
                    return True
        finally:
            ole.close()

        return False

    except Exception as e:
        logger.debug(f"OLE thumbnail extraction failed for {source_path.name}: {e}")
        return False


def generate_thumbnail_from_pdf(pdf_path: Path, width: int, height: int, is_dwg: bool = False) -> Optional[Image.Image]:
    """Generate thumbnail from PDF, with optional DWG content-aware cropping."""
    try:
        if is_dwg:
            # DWG: Use high-res intermediate for content detection
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=settings.DWG_INTERMEDIATE_DPI)
            if not images:
                return None
            img = images[0]
            # Crop to content before scaling
            img = crop_to_content(img)
        else:
            # Regular PDF: standard DPI
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=150)
            if not images:
                return None
            img = images[0]
        
        return create_cover_thumbnail(img, width, height)
    except Exception as e:
        logger.error(f"Failed to convert PDF to thumbnail: {e}")
        return None


def generate_thumbnail(source_path: Path, dest_path: Path, original_filename: str, temp_dir: Optional[Path] = None) -> bool:
    """Generate thumbnail for image, PDF, or DWG."""
    try:
        ext = get_file_extension(source_path.name)
        width, height = get_thumbnail_dimensions(original_filename)

        if ext in settings.THUMBNAIL_DWG_EXTENSIONS:
            # DWG/DXF: Convert to PDF via QCAD, then use content-aware processing
            pdf_path = convert_dwg_to_pdf(source_path)
            if not pdf_path:
                return False
            thumbnail = generate_thumbnail_from_pdf(pdf_path, width, height, is_dwg=True)
            pdf_path.unlink(missing_ok=True)
            if thumbnail is None:
                return False
            thumbnail.save(dest_path, "PNG", optimize=True)
            return True
        elif ext in settings.THUMBNAIL_PDF_EXTENSIONS:
            # PDF: Convert first page to image
            thumbnail = generate_thumbnail_from_pdf(source_path, width, height, is_dwg=False)
            if thumbnail is None:
                return False
            thumbnail.save(dest_path, "PNG", optimize=True)
            return True
        else:
            # Image file
            img = Image.open(source_path)
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
        # Remove null bytes - PostgreSQL TEXT fields don't accept them
        full_text = full_text.replace('\x00', '')
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


def extract_text_fallback(source_path: Path) -> Optional[str]:
    """
    Try to extract text from unknown file types.
    Only succeeds if the file looks like valid text (high printable char ratio, no null bytes).
    Works for plain text formats like .ifc, .nvtm, etc.
    """
    try:
        # Skip files that are too large
        file_size = source_path.stat().st_size
        if file_size > settings.TEXT_FALLBACK_MAX_SIZE:
            return None

        # Read raw bytes first to check for binary content
        with open(source_path, "rb") as f:
            raw_data = f.read(min(file_size, settings.MAX_TEXT_LENGTH))

        # Reject if contains null bytes (strong indicator of binary)
        if b'\x00' in raw_data:
            return None

        # Try to decode as UTF-8
        try:
            text = raw_data.decode("utf-8")
        except UnicodeDecodeError:
            # Try latin-1 as fallback
            try:
                text = raw_data.decode("latin-1")
            except UnicodeDecodeError:
                return None

        if not text.strip():
            return None

        # Check printable character ratio
        printable_chars = sum(1 for c in text if c.isprintable() or c in '\n\r\t')
        printable_ratio = printable_chars / len(text) if text else 0

        if printable_ratio < settings.TEXT_FALLBACK_MIN_PRINTABLE:
            return None

        # Remove null bytes just in case (PostgreSQL TEXT doesn't accept them)
        text = text.replace('\x00', '')

        logger.info(f"Extracted text from unknown format {source_path.name} ({len(text)} chars, {printable_ratio:.0%} printable)")
        return text

    except Exception as e:
        logger.debug(f"Text fallback extraction failed for {source_path.name}: {e}")
        return None


def process_file(source_path: Path, temp_dir: Path, original_filename: Optional[str] = None) -> Tuple[Optional[Path], Optional[str]]:
    """
    Process a file: generate thumbnail and extract text.
    Returns (thumbnail_path, extracted_text).
    original_filename is used to determine thumbnail dimensions (uses source_path.name if not provided).
    """
    thumbnail_path = None
    extracted_text = None
    filename = original_filename or source_path.name
    width, height = get_thumbnail_dimensions(filename)

    # Special handling for DWG: convert once, use for both thumbnail and text
    if is_dwg(source_path.name):
        pdf_path = convert_dwg_to_pdf(source_path)
        if pdf_path:
            thumb_name = f"{uuid.uuid4()}.png"
            thumb_path = temp_dir / thumb_name
            thumbnail = generate_thumbnail_from_pdf(pdf_path, width, height, is_dwg=True)
            if thumbnail:
                thumbnail.save(thumb_path, "PNG", optimize=True)
                thumbnail_path = thumb_path
            extracted_text = extract_text_from_pdf(pdf_path)
            pdf_path.unlink(missing_ok=True)
        return thumbnail_path, extracted_text

    # Special handling for Office docs: convert to PDF, then process
    if is_office(source_path.name):
        pdf_path = convert_office_to_pdf(source_path, temp_dir)
        if pdf_path:
            thumb_name = f"{uuid.uuid4()}.png"
            thumb_path = temp_dir / thumb_name
            thumbnail = generate_thumbnail_from_pdf(pdf_path, width, height, is_dwg=False)
            if thumbnail:
                thumbnail.save(thumb_path, "PNG", optimize=True)
                thumbnail_path = thumb_path
            extracted_text = extract_text_from_pdf(pdf_path)
            pdf_path.unlink(missing_ok=True)
        return thumbnail_path, extracted_text

    # Standard processing for known file types
    if can_generate_thumbnail(source_path.name):
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if generate_thumbnail(source_path, thumb_path, filename, temp_dir):
            thumbnail_path = thumb_path

    if can_extract_text(source_path.name):
        extracted_text = extract_text(source_path)

    # Fallback: try extracting thumbnail from zip-based formats (any file type)
    if thumbnail_path is None:
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if extract_archive_thumbnail(source_path, thumb_path, width, height):
            thumbnail_path = thumb_path

    # Fallback: try extracting thumbnail from OLE compound documents
    if thumbnail_path is None:
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if extract_ole_thumbnail(source_path, thumb_path, width, height):
            thumbnail_path = thumb_path

    # Fallback: try extracting text from unknown file types
    if extracted_text is None:
        extracted_text = extract_text_fallback(source_path)

    return thumbnail_path, extracted_text

