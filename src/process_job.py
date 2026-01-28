"""Air-gapped job processor - reads from /work, writes results to /work.

This runs in a fresh container with NO network access.
Input: /work/input.bin (file to process) + /work/job.json (metadata)
Output: /work/result.json + /work/thumbnail.png (if generated)

Logs are written to /work/processor.log for the uploader to read and forward.
"""
import json
import logging
import sys
from pathlib import Path

# Configure logging to file (uploader will read and forward)
log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    log_handlers.append(logging.FileHandler("/work/processor.log"))
except Exception:
    pass  # /work might not be writable yet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("processor")

# Import after logging setup
from src.processor import process_file


def main():
    work_dir = Path("/work")
    job_file = work_dir / "job.json"
    input_file = work_dir / "input.bin"
    result_file = work_dir / "result.json"
    
    if not job_file.exists():
        logger.error("No job.json found in /work")
        sys.exit(1)
    
    if not input_file.exists():
        logger.error("No input.bin found in /work")
        sys.exit(1)
    
    job = json.loads(job_file.read_text())
    content_hash = job.get("content_hash", "unknown")
    original_extension = job.get("original_extension", "")
    original_filename = job.get("original_filename", f"file{original_extension}")
    
    logger.info(f"Processing: {original_filename} ({content_hash[:8]})")
    
    # Rename input to have correct extension for processing hints
    actual_input = work_dir / f"input{original_extension}"
    input_file.rename(actual_input)
    
    result = {
        "content_hash": content_hash,
        "success": False,
        "thumbnail_file": None,
        "extracted_text": None,
        "error": None,
    }
    
    try:
        thumbnail_path, extracted_text = process_file(actual_input, work_dir, original_filename, original_extension)
        
        result["success"] = True
        if thumbnail_path and thumbnail_path.exists():
            # Move thumbnail to standard location
            final_thumb = work_dir / "thumbnail.png"
            thumbnail_path.rename(final_thumb)
            result["thumbnail_file"] = "thumbnail.png"
            logger.info(f"Generated thumbnail: {final_thumb.stat().st_size} bytes")
        
        if extracted_text:
            result["extracted_text"] = extracted_text
            logger.info(f"Extracted text: {len(extracted_text)} chars")
        
        if not result["thumbnail_file"] and not result["extracted_text"]:
            logger.info("No thumbnail or text generated for this file type")
        
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Processing failed: {e}", exc_info=True)
    
    finally:
        # Cleanup temp files
        actual_input.unlink(missing_ok=True)
        for f in work_dir.glob("*.pdf"):
            if f.name != "thumbnail.png":
                f.unlink(missing_ok=True)
    
    result_file.write_text(json.dumps(result, indent=2))
    logger.info(f"Job completed: success={result['success']}")


if __name__ == "__main__":
    main()

