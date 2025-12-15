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


def convert_dwg_to_pdf(source_path: Path, temp_dir: Path) -> Optional[Path]:
    """Convert DWG/DXF to PDF using ODA File Converter."""
    try:
        # Check if converter exists
        converter_path = Path(settings.ODA_CONVERTER_PATH)
        if not converter_path.exists():
            logger.error(f"ODA File Converter not found at {settings.ODA_CONVERTER_PATH}")
            return None
        
        # ODA works on folders, so copy file to isolated temp folder
        job_id = uuid.uuid4()
        input_dir = temp_dir / f"dwg_in_{job_id}"
        output_dir = temp_dir / f"dwg_out_{job_id}"
        input_dir.mkdir(exist_ok=True)
        output_dir.mkdir(exist_ok=True)
        
        # Copy source file to input dir
        temp_dwg = input_dir / source_path.name
        shutil.copy2(source_path, temp_dwg)
        
        logger.info(f"Converting DWG: {source_path.name} using ODA File Converter")
        
        # ODA File Converter args: input_folder output_folder output_version output_type recurse audit
        result = subprocess.run(
            [
                settings.ODA_CONVERTER_PATH,
                str(input_dir),
                str(output_dir),
                "ACAD2018",  # Output version
                "PDF",       # Output format
                "0",         # Don't recurse
                "0",         # Don't audit
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        logger.debug(f"ODA return code: {result.returncode}")
        if result.stdout:
            logger.debug(f"ODA stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.debug(f"ODA stderr: {result.stderr[:500]}")
        
        # Cleanup input
        temp_dwg.unlink(missing_ok=True)
        input_dir.rmdir()
        
        # Find generated PDF
        pdf_name = source_path.stem + ".pdf"
        pdf_path = output_dir / pdf_name
        
        if pdf_path.exists():
            logger.info(f"DWG conversion successful: {source_path.name} -> PDF")
            return pdf_path
        
        # Check if any PDF was created (maybe different name)
        pdfs = list(output_dir.glob("*.pdf"))
        if pdfs:
            logger.info(f"DWG conversion successful (different name): {pdfs[0].name}")
            return pdfs[0]
        
        # Log what went wrong
        logger.error(f"DWG conversion produced no PDF for {source_path.name}")
        logger.error(f"ODA return code: {result.returncode}")
        if result.stdout:
            logger.error(f"ODA stdout: {result.stdout}")
        if result.stderr:
            logger.error(f"ODA stderr: {result.stderr}")
        
        # Cleanup output dir
        shutil.rmtree(output_dir, ignore_errors=True)
        return None
        
    except subprocess.TimeoutExpired:
        logger.error(f"DWG conversion timed out for {source_path.name}")
        return None
    except FileNotFoundError as e:
        logger.error(f"ODA File Converter not found: {e}")
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
            # DWG/DXF: Convert to PDF first, then to image
            if temp_dir is None:
                temp_dir = settings.TEMP_DIR
            pdf_path = convert_dwg_to_pdf(source_path, temp_dir)
            if not pdf_path:
                return False
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=150)
            # Cleanup temp PDF
            pdf_path.unlink(missing_ok=True)
            pdf_path.parent.rmdir()
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

