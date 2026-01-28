"""OCR Client - File-based IPC with OCR sidecar.

Used by the processor container to request OCR from the persistent OCR sidecar.
Communication is via shared /ocr-exchange volume.

Protocol:
1. Write image to /ocr-exchange/{job_id}.png
2. Write request JSON to /ocr-exchange/{job_id}.request
3. Poll for /ocr-exchange/{job_id}.result or .failed
4. Read result, cleanup files
"""
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration
OCR_EXCHANGE_DIR = Path("/ocr-exchange")
OCR_TIMEOUT = 300  # 5 minutes max per OCR request


# Extensions that indicate PDF was generated from another format (text is always good)
GENERATED_PDF_SOURCES = {
    '.dwg', '.dxf',  # CAD
    '.xlsx', '.xls', '.xlsm', '.ods',  # Spreadsheets
    '.docx', '.doc', '.docm', '.odt',  # Word processors
    '.pptx', '.ppt', '.pptm', '.odp',  # Presentations
    '.pages', '.numbers', '.key',  # Apple iWork
}


def needs_ocr_check(original_extension: str) -> bool:
    """Returns False if PDF was generated from a source with perfect text."""
    return original_extension.lower() not in GENERATED_PDF_SOURCES


def request_ocr(image_path: Path) -> Optional[dict]:
    """
    Request OCR from the sidecar for an image file.
    
    Returns dict with:
        - text: str - extracted text
        - confidence: float - average OCR confidence (0-1)
        - quality: float - wordlist quality score (0-1)
        - word_count: int
        - char_count: int
    
    Returns None on failure.
    """
    job_id = str(uuid.uuid4())[:12]
    
    # Copy image to exchange directory
    exchange_image = OCR_EXCHANGE_DIR / f"{job_id}.png"
    request_file = OCR_EXCHANGE_DIR / f"{job_id}.request"
    result_file = OCR_EXCHANGE_DIR / f"{job_id}.result"
    failed_file = OCR_EXCHANGE_DIR / f"{job_id}.failed"
    
    try:
        # Copy image to exchange
        shutil.copy2(image_path, exchange_image)
        
        # Write request
        request_data = {
            "image_path": f"{job_id}.png",
            "job_id": job_id,
        }
        request_file.write_text(json.dumps(request_data))
        
        # Wait for result
        start = time.time()
        while time.time() - start < OCR_TIMEOUT:
            if result_file.exists():
                result = json.loads(result_file.read_text())
                # Cleanup
                result_file.unlink(missing_ok=True)
                exchange_image.unlink(missing_ok=True)
                return result
            
            if failed_file.exists():
                error = failed_file.read_text()
                failed_file.unlink(missing_ok=True)
                exchange_image.unlink(missing_ok=True)
                logger.warning(f"OCR failed: {error[:200]}")
                return None
            
            time.sleep(0.5)
        
        logger.error(f"OCR timeout for {image_path.name}")
        # Cleanup on timeout
        request_file.unlink(missing_ok=True)
        exchange_image.unlink(missing_ok=True)
        return None
        
    except Exception as e:
        logger.error(f"OCR request failed: {e}")
        # Cleanup on error
        request_file.unlink(missing_ok=True)
        exchange_image.unlink(missing_ok=True)
        return None


def should_use_ocr(embedded_text: str, ocr_result: dict) -> tuple[bool, str]:
    """
    Decide if OCR text should replace/augment embedded text.
    
    Returns (should_use_ocr, reason)
    
    Philosophy: When in doubt, prefer OCR. Better to have redundant 
    good text than miss searchable content.
    """
    ocr_text = ocr_result.get("text", "")
    ocr_quality = ocr_result.get("quality", 0.5)
    
    emb_len = len(embedded_text.strip()) if embedded_text else 0
    ocr_len = len(ocr_text.strip())
    
    # === CASE 1: Embedded has nothing ===
    if emb_len < 10:
        if ocr_len > 50:
            return True, "no_embedded_ocr_found_text"
        else:
            return False, "both_empty"
    
    # === CASE 2: OCR found significantly more ===
    if ocr_len > emb_len * 2 and ocr_len > 200:
        return True, "ocr_found_more"
    
    # === CASE 3: Quality comparison ===
    if ocr_len > 100 and ocr_quality > 0.4:
        # We don't have embedded quality here, so use a simpler heuristic:
        # If OCR has good quality AND found substantial text, prefer it
        # when embedded is short
        if emb_len < 500 and ocr_quality > 0.5:
            return True, "ocr_better_for_short_embedded"
    
    # === DEFAULT: Embedded is acceptable ===
    return False, "embedded_ok"


def get_final_text(embedded_text: str, ocr_result: Optional[dict], reason: str) -> str:
    """
    Determine final extracted text based on OCR decision.
    
    When uncertain or OCR is better, may concatenate both for completeness.
    """
    embedded = embedded_text.strip() if embedded_text else ""
    ocr_text = ocr_result.get("text", "").strip() if ocr_result else ""
    
    if reason == "both_empty":
        return ""
    
    if reason == "embedded_ok":
        return embedded
    
    if reason in ("ocr_found_more", "ocr_better_for_short_embedded", "no_embedded_ocr_found_text"):
        # OCR is primary
        if embedded and len(embedded) > 50 and reason != "no_embedded_ocr_found_text":
            # Concatenate: OCR first (better), then embedded (might have unique content)
            return f"{ocr_text}\n\n--- embedded text ---\n\n{embedded}"
        return ocr_text
    
    # Fallback
    return embedded if embedded else ocr_text
