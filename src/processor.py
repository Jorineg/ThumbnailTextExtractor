"""File processing: thumbnail generation and text extraction.

Air-gapped design: NO network calls, NO credentials, file-based I/O only.
DWG conversion uses file-based IPC with QCAD sidecar via shared volume.
"""
import shutil
import subprocess
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple
import logging

import numpy as np
from PIL import Image
import pillow_heif
import fitz  # PyMuPDF
import olefile
import cairosvg
from pdf2image import convert_from_path

pillow_heif.register_heif_opener()

# Use processor_settings in air-gapped mode, fall back to main settings
try:
    from src import processor_settings as settings
except ImportError:
    from src import settings

logger = logging.getLogger(__name__)


def get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_image(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_IMAGE_EXTENSIONS


def is_pdf(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_PDF_EXTENSIONS


def is_dwg(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_DWG_EXTENSIONS


def is_office(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_OFFICE_EXTENSIONS


def is_svg(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_SVG_EXTENSIONS


def is_video(filename: str) -> bool:
    return get_file_extension(filename) in settings.THUMBNAIL_VIDEO_EXTENSIONS


def is_text_file(filename: str) -> bool:
    return get_file_extension(filename) in settings.TEXT_EXTRACT_EXTENSIONS


def can_generate_thumbnail(filename: str) -> bool:
    return is_image(filename) or is_pdf(filename) or is_dwg(filename) or is_office(filename) or is_svg(filename) or is_video(filename)


def get_thumbnail_dimensions(filename: str) -> Tuple[int, int]:
    ext = get_file_extension(filename)
    if ext in settings.THUMBNAIL_SMALL_EXTENSIONS:
        return settings.THUMBNAIL_WIDTH, settings.THUMBNAIL_HEIGHT
    return settings.THUMBNAIL_LARGE_WIDTH, settings.THUMBNAIL_LARGE_HEIGHT


def can_extract_text(filename: str) -> bool:
    return is_pdf(filename) or is_text_file(filename)


def create_cover_thumbnail(img: Image.Image, width: int, height: int) -> Image.Image:
    target_ratio = width / height
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        new_width = int(img.height * target_ratio)
        left = (img.width - new_width) // 2
        img = img.crop((left, 0, left + new_width, img.height))
    else:
        new_height = int(img.width / target_ratio)
        top = 0 if settings.THUMBNAIL_CROP_POSITION == "top" else (img.height - new_height) // 2
        img = img.crop((0, top, img.width, top + new_height))

    return img.resize((width, height), Image.Resampling.LANCZOS)


def convert_dwg_to_pdf(source_path: Path) -> Optional[Path]:
    """Convert DWG/DXF to PDF using file-based IPC with QCAD sidecar.
    
    Protocol:
    1. Copy DWG to /dwg-exchange/{job_id}.dwg
    2. Create /dwg-exchange/{job_id}.convert (signal file)
    3. QCAD sidecar sees .convert, processes, creates .done or .failed
    4. Read result PDF or error
    """
    job_id = str(uuid.uuid4())
    exchange_dir = Path(settings.DWG_EXCHANGE_DIR)
    
    dwg_name = f"{job_id}{source_path.suffix}"
    exchange_dwg = exchange_dir / dwg_name
    pdf_name = f"{job_id}.pdf"
    exchange_pdf = exchange_dir / pdf_name
    signal_file = exchange_dir / f"{job_id}.convert"
    done_file = exchange_dir / f"{job_id}.done"
    failed_file = exchange_dir / f"{job_id}.failed"
    
    try:
        shutil.copy2(source_path, exchange_dwg)
        signal_file.write_text(dwg_name)  # Signal contains input filename
        
        # Wait for QCAD to process (up to 5 minutes)
        timeout = 300
        start = time.time()
        while time.time() - start < timeout:
            if done_file.exists():
                done_file.unlink()
                exchange_dwg.unlink(missing_ok=True)
                if exchange_pdf.exists():
                    logger.info(f"DWG converted to PDF: {source_path.name}")
                    return exchange_pdf
                logger.warning(f"QCAD done but no PDF found for {source_path.name}")
                return None
            
            if failed_file.exists():
                error = failed_file.read_text()
                failed_file.unlink()
                exchange_dwg.unlink(missing_ok=True)
                logger.warning(f"QCAD conversion failed for {source_path.name}: {error[:500]}")
                return None
            
            time.sleep(0.5)
        
        logger.error(f"DWG conversion timed out for {source_path.name}")
        signal_file.unlink(missing_ok=True)
        exchange_dwg.unlink(missing_ok=True)
        return None
        
    except Exception as e:
        logger.error(f"DWG conversion failed for {source_path.name}: {e}", exc_info=True)
        signal_file.unlink(missing_ok=True)
        exchange_dwg.unlink(missing_ok=True)
        return None


def convert_office_to_pdf(source_path: Path, temp_dir: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(temp_dir), str(source_path)],
            capture_output=True, text=True, timeout=120
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


def convert_svg_to_image(source_path: Path, width: int) -> Optional[Image.Image]:
    try:
        png_data = cairosvg.svg2png(url=str(source_path), output_width=width * 2)
        img = Image.open(BytesIO(png_data))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        logger.info(f"SVG converted to image: {source_path.name}")
        return img
    except Exception as e:
        logger.error(f"SVG conversion failed for {source_path.name}: {e}")
        return None


def extract_video_frame(source_path: Path, temp_dir: Path) -> Optional[Path]:
    try:
        frame_path = temp_dir / f"{uuid.uuid4()}.png"
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source_path), "-ss", "00:00:01", "-frames:v", "1", "-q:v", "2", str(frame_path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and frame_path.exists():
            logger.info(f"Video frame extracted: {source_path.name}")
            return frame_path
        # Fallback: first frame
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source_path), "-frames:v", "1", "-q:v", "2", str(frame_path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and frame_path.exists():
            logger.info(f"Video frame extracted (first frame): {source_path.name}")
            return frame_path
        logger.warning(f"ffmpeg failed for {source_path.name}: {result.stderr[:500] if result.stderr else 'no output'}")
        return None
    except subprocess.TimeoutExpired:
        logger.error(f"Video frame extraction timed out for {source_path.name}")
        return None
    except Exception as e:
        logger.error(f"Video frame extraction failed for {source_path.name}: {e}")
        return None


def find_content_bounds(img: Image.Image, threshold: int = 250) -> Tuple[int, int, int, int]:
    gray = img.convert("L")
    arr = np.array(gray)
    non_white = arr < threshold
    
    if not non_white.any():
        return 0, 0, img.width, img.height
    
    rows = np.any(non_white, axis=1)
    cols = np.any(non_white, axis=0)
    
    top = np.argmax(rows)
    bottom = len(rows) - np.argmax(rows[::-1])
    left = np.argmax(cols)
    right = len(cols) - np.argmax(cols[::-1])
    
    return left, top, right, bottom


def find_gap_splits(has_content: np.ndarray, gap_threshold_ratio: float = 0.15) -> list[int]:
    if not has_content.any():
        return []
    
    first = np.argmax(has_content)
    last = len(has_content) - np.argmax(has_content[::-1])
    content_span = last - first
    
    if content_span <= 0:
        return []
    
    gap_threshold = int(content_span * gap_threshold_ratio)
    if gap_threshold < 10:
        return []
    
    splits = []
    in_gap = False
    gap_start = 0
    
    for i in range(first, last):
        if not has_content[i]:
            if not in_gap:
                in_gap = True
                gap_start = i
        else:
            if in_gap:
                gap_size = i - gap_start
                if gap_size >= gap_threshold:
                    splits.append(i)
                in_gap = False
    
    return splits


def find_regions_from_splits(has_content: np.ndarray, splits: list[int]) -> list[Tuple[int, int]]:
    if not has_content.any():
        return []
    
    first = np.argmax(has_content)
    last = len(has_content) - np.argmax(has_content[::-1])
    
    if not splits:
        return [(first, last)]
    
    regions = []
    prev = first
    for split in splits:
        region_end = split
        while region_end > prev and not has_content[region_end - 1]:
            region_end -= 1
        if region_end > prev:
            regions.append((prev, region_end))
        prev = split
    
    if prev < last:
        regions.append((prev, last))
    
    return regions


def find_largest_content_region(img: Image.Image, threshold: int = 250) -> Tuple[int, int, int, int]:
    gray = img.convert("L")
    arr = np.array(gray)
    non_white = arr < threshold
    
    if not non_white.any():
        return 0, 0, img.width, img.height
    
    row_has_content = np.any(non_white, axis=1)
    col_has_content = np.any(non_white, axis=0)
    
    row_splits = find_gap_splits(row_has_content)
    col_splits = find_gap_splits(col_has_content)
    
    row_regions = find_regions_from_splits(row_has_content, row_splits)
    col_regions = find_regions_from_splits(col_has_content, col_splits)
    
    if not row_regions or not col_regions:
        return find_content_bounds(img, threshold)
    
    if len(row_regions) == 1 and len(col_regions) == 1:
        return find_content_bounds(img, threshold)
    
    best_region = None
    best_content = 0
    
    for (row_start, row_end) in row_regions:
        for (col_start, col_end) in col_regions:
            region_content = np.sum(non_white[row_start:row_end, col_start:col_end])
            if region_content > best_content:
                best_content = region_content
                best_region = (col_start, row_start, col_end, row_end)
    
    if best_region is None:
        return find_content_bounds(img, threshold)
    
    logger.debug(f"Content region analysis: {len(row_regions)} row regions, {len(col_regions)} col regions, selected {best_region}")
    return best_region


def crop_to_content(img: Image.Image, margin_ratio: float = 0.02) -> Image.Image:
    left, top, right, bottom = find_largest_content_region(img, settings.DWG_WHITE_THRESHOLD)
    
    content_width = right - left
    content_height = bottom - top
    
    if content_width <= 0 or content_height <= 0:
        return img
    
    margin_x = int(content_width * margin_ratio)
    margin_y = int(content_height * margin_ratio)
    
    left = max(0, left - margin_x)
    top = max(0, top - margin_y)
    right = min(img.width, right + margin_x)
    bottom = min(img.height, bottom + margin_y)
    
    cropped = img.crop((left, top, right, bottom))
    logger.debug(f"Content crop: {img.width}x{img.height} -> {cropped.width}x{cropped.height}")
    return cropped


def extract_archive_thumbnail(source_path: Path, dest_path: Path, width: int, height: int) -> bool:
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
    try:
        if not olefile.isOleFile(source_path):
            return False

        ole = olefile.OleFileIO(source_path)
        try:
            if ole.exists('BITMAP'):
                bmp_data = ole.openstream('BITMAP').read()
                if bmp_data[:2] == b'BM':
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
    try:
        if is_dwg:
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=settings.DWG_INTERMEDIATE_DPI)
            if not images:
                return None
            img = images[0]
            img = crop_to_content(img)
        else:
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1, dpi=150)
            if not images:
                return None
            img = images[0]
        
        return create_cover_thumbnail(img, width, height)
    except Exception as e:
        logger.error(f"Failed to convert PDF to thumbnail: {e}")
        return None


def generate_thumbnail(source_path: Path, dest_path: Path, original_filename: str, temp_dir: Optional[Path] = None) -> bool:
    try:
        ext = get_file_extension(source_path.name)
        width, height = get_thumbnail_dimensions(original_filename)

        if ext in settings.THUMBNAIL_DWG_EXTENSIONS:
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
            thumbnail = generate_thumbnail_from_pdf(source_path, width, height, is_dwg=False)
            if thumbnail is None:
                return False
            thumbnail.save(dest_path, "PNG", optimize=True)
            return True
        else:
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
    try:
        doc = fitz.open(source_path)
        text_parts = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                text_parts.append(text)
        doc.close()
        
        full_text = "\n\n".join(text_parts)
        full_text = full_text.replace('\x00', '')
        if len(full_text) > settings.MAX_TEXT_LENGTH:
            full_text = full_text[:settings.MAX_TEXT_LENGTH]
        return full_text if full_text.strip() else None

    except Exception as e:
        logger.error(f"Failed to extract PDF text from {source_path.name}: {e}")
        return None


def extract_text_from_pdf_page(source_path: Path, page_num: int = 0) -> Optional[str]:
    """Extract text from a specific PDF page."""
    try:
        doc = fitz.open(source_path)
        if page_num >= len(doc):
            doc.close()
            return None
        text = doc[page_num].get_text()
        doc.close()
        return text.replace('\x00', '') if text.strip() else None
    except Exception as e:
        logger.debug(f"Failed to extract text from page {page_num}: {e}")
        return None


def render_pdf_page_to_image(source_path: Path, page_num: int = 0, dpi: int = 200) -> Optional[Path]:
    """Render a PDF page to an image file for OCR."""
    try:
        images = convert_from_path(str(source_path), first_page=page_num + 1, 
                                   last_page=page_num + 1, dpi=dpi)
        if not images:
            return None
        
        # Save to temp file
        temp_path = source_path.parent / f"ocr_page_{page_num}_{uuid.uuid4()}.png"
        images[0].save(temp_path, "PNG")
        return temp_path
    except Exception as e:
        logger.debug(f"Failed to render PDF page {page_num} to image: {e}")
        return None


def ocr_image(image_path: Path) -> Optional[dict]:
    """Request OCR for an image via the OCR sidecar."""
    try:
        from src.ocr_client import request_ocr
        return request_ocr(image_path)
    except ImportError:
        logger.debug("OCR client not available")
        return None
    except Exception as e:
        logger.error(f"OCR request failed: {e}")
        return None


def process_pdf_with_ocr(source_path: Path, original_extension: str) -> Optional[str]:
    """
    Process PDF text extraction with OCR quality comparison.
    
    1. Extract embedded text from all pages
    2. If original was DWG/Office, skip OCR (text is always good)
    3. Otherwise, OCR page 1 and compare quality
    4. If OCR is better, run OCR on all pages
    """
    try:
        from src.ocr_client import needs_ocr_check, should_use_ocr, get_final_text
    except ImportError:
        logger.debug("OCR client not available, using embedded text only")
        return extract_text_from_pdf(source_path)
    
    # Get embedded text
    embedded_text = extract_text_from_pdf(source_path)
    
    # Skip OCR check for generated PDFs (DWG, Office conversions)
    if not needs_ocr_check(original_extension):
        logger.debug(f"Skipping OCR check for generated PDF (from {original_extension})")
        return embedded_text
    
    # Get embedded text from page 1 for comparison
    page1_embedded = extract_text_from_pdf_page(source_path, 0)
    
    # Render page 1 to image and run OCR
    page1_image = render_pdf_page_to_image(source_path, 0)
    if not page1_image:
        logger.debug("Could not render PDF page for OCR comparison")
        return embedded_text
    
    try:
        ocr_result = ocr_image(page1_image)
    finally:
        page1_image.unlink(missing_ok=True)
    
    if not ocr_result:
        logger.debug("OCR comparison failed, using embedded text")
        return embedded_text
    
    # Decide if we should use OCR
    use_ocr, reason = should_use_ocr(page1_embedded, ocr_result)
    logger.info(f"OCR decision: {reason} (embedded={len(page1_embedded or '')} chars, "
                f"ocr={ocr_result.get('char_count', 0)} chars, quality={ocr_result.get('quality', 0):.2f})")
    
    if not use_ocr:
        return embedded_text
    
    # OCR is better - run on all pages
    logger.info("Running OCR on all PDF pages...")
    doc = fitz.open(source_path)
    page_count = len(doc)
    doc.close()
    
    ocr_texts = []
    for page_num in range(page_count):
        page_image = render_pdf_page_to_image(source_path, page_num)
        if page_image:
            try:
                page_ocr = ocr_image(page_image)
                if page_ocr and page_ocr.get("text"):
                    ocr_texts.append(page_ocr["text"])
            finally:
                page_image.unlink(missing_ok=True)
    
    full_ocr_text = "\n\n".join(ocr_texts) if ocr_texts else ""
    
    # Get final text (may concatenate both)
    final_text = get_final_text(embedded_text, {"text": full_ocr_text}, reason)
    
    if len(final_text) > settings.MAX_TEXT_LENGTH:
        final_text = final_text[:settings.MAX_TEXT_LENGTH]
    
    logger.info(f"OCR complete: {len(final_text)} chars from {page_count} pages")
    return final_text if final_text.strip() else None


def process_image_with_ocr(source_path: Path) -> Optional[str]:
    """Run OCR on an image file."""
    ocr_result = ocr_image(source_path)
    if ocr_result and ocr_result.get("text"):
        text = ocr_result["text"]
        if len(text) > settings.MAX_TEXT_LENGTH:
            text = text[:settings.MAX_TEXT_LENGTH]
        logger.info(f"Image OCR: {len(text)} chars, quality={ocr_result.get('quality', 0):.2f}")
        return text
    return None


def extract_text_from_file(source_path: Path) -> Optional[str]:
    try:
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
    if is_pdf(source_path.name):
        return extract_text_from_pdf(source_path)
    elif is_text_file(source_path.name):
        return extract_text_from_file(source_path)
    return None


def extract_text_fallback(source_path: Path) -> Optional[str]:
    try:
        file_size = source_path.stat().st_size
        if file_size > settings.TEXT_FALLBACK_MAX_SIZE:
            return None

        with open(source_path, "rb") as f:
            raw_data = f.read(min(file_size, settings.MAX_TEXT_LENGTH))

        if b'\x00' in raw_data:
            return None

        try:
            text = raw_data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw_data.decode("latin-1")
            except UnicodeDecodeError:
                return None

        if not text.strip():
            return None

        printable_chars = sum(1 for c in text if c.isprintable() or c in '\n\r\t')
        printable_ratio = printable_chars / len(text) if text else 0

        if printable_ratio < settings.TEXT_FALLBACK_MIN_PRINTABLE:
            return None

        text = text.replace('\x00', '')

        logger.info(f"Extracted text from unknown format {source_path.name} ({len(text)} chars, {printable_ratio:.0%} printable)")
        return text

    except Exception as e:
        logger.debug(f"Text fallback extraction failed for {source_path.name}: {e}")
        return None


def process_file(source_path: Path, temp_dir: Path, original_filename: Optional[str] = None, 
                  original_extension: Optional[str] = None) -> Tuple[Optional[Path], Optional[str]]:
    """Process a file: generate thumbnail and extract text (with OCR when appropriate)."""
    thumbnail_path = None
    extracted_text = None
    filename = original_filename or source_path.name
    orig_ext = original_extension or get_file_extension(filename)
    width, height = get_thumbnail_dimensions(filename)

    # DWG: convert once, use for both (no OCR needed - generated PDF has perfect text)
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

    # Office: convert to PDF, then process (no OCR needed - generated PDF has perfect text)
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

    # SVG: convert via cairosvg (no text to extract)
    if is_svg(source_path.name):
        img = convert_svg_to_image(source_path, width)
        if img:
            thumb_name = f"{uuid.uuid4()}.png"
            thumb_path = temp_dir / thumb_name
            thumbnail = create_cover_thumbnail(img, width, height)
            thumbnail.save(thumb_path, "PNG", optimize=True)
            thumbnail_path = thumb_path
        return thumbnail_path, None

    # Video: extract frame (no text to extract)
    if is_video(source_path.name):
        frame_path = extract_video_frame(source_path, temp_dir)
        if frame_path:
            thumb_name = f"{uuid.uuid4()}.png"
            thumb_path = temp_dir / thumb_name
            img = Image.open(frame_path)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            thumbnail = create_cover_thumbnail(img, width, height)
            thumbnail.save(thumb_path, "PNG", optimize=True)
            thumbnail_path = thumb_path
            frame_path.unlink(missing_ok=True)
        return thumbnail_path, None

    # Images: generate thumbnail AND run OCR
    if is_image(source_path.name):
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if generate_thumbnail(source_path, thumb_path, filename, temp_dir):
            thumbnail_path = thumb_path
        # Always OCR images
        extracted_text = process_image_with_ocr(source_path)
        return thumbnail_path, extracted_text

    # PDF: generate thumbnail and extract text with OCR comparison
    if is_pdf(source_path.name):
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if generate_thumbnail(source_path, thumb_path, filename, temp_dir):
            thumbnail_path = thumb_path
        # Extract text with OCR quality comparison
        extracted_text = process_pdf_with_ocr(source_path, orig_ext)
        return thumbnail_path, extracted_text

    # Text files: extract directly (no OCR needed)
    if is_text_file(source_path.name):
        extracted_text = extract_text_from_file(source_path)
        return None, extracted_text

    # Fallback: zip-based formats (thumbnails only)
    thumb_name = f"{uuid.uuid4()}.png"
    thumb_path = temp_dir / thumb_name
    if extract_archive_thumbnail(source_path, thumb_path, width, height):
        thumbnail_path = thumb_path

    # Fallback: OLE compound documents (thumbnails only)
    if thumbnail_path is None:
        thumb_name = f"{uuid.uuid4()}.png"
        thumb_path = temp_dir / thumb_name
        if extract_ole_thumbnail(source_path, thumb_path, width, height):
            thumbnail_path = thumb_path

    # Fallback: unknown text formats
    if extracted_text is None:
        extracted_text = extract_text_fallback(source_path)

    return thumbnail_path, extracted_text
