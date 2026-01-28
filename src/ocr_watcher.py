"""OCR Sidecar Watcher - Persistent air-gapped OCR service.

File-based IPC protocol (same pattern as QCAD):
1. Processor writes image to /ocr-exchange/{job_id}.png
2. Processor creates /ocr-exchange/{job_id}.request (contains metadata JSON)
3. This watcher processes, writes /ocr-exchange/{job_id}.result (JSON with text)
4. On error, writes /ocr-exchange/{job_id}.failed (error message)
5. Processor reads result and cleans up

Request JSON format:
{
    "image_path": "{job_id}.png",
    "job_id": "abc123"
}

Result JSON format:
{
    "text": "extracted text here",
    "confidence": 0.85,
    "word_count": 42
}
"""
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import easyocr

# Configuration
OCR_EXCHANGE_DIR = Path(os.getenv("OCR_EXCHANGE_DIR", "/ocr-exchange"))
WORDLIST_PATH = Path(os.getenv("WORDLIST_PATH", "/app/wordlists/combined.txt"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ocr-watcher")


class OCRWatcher:
    def __init__(self):
        self.running = True
        self.reader = None
        self.wordlist = set()

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def load_model(self):
        """Load EasyOCR model (done once at startup)."""
        logger.info("Loading EasyOCR model (German + English)...")
        start = time.time()
        self.reader = easyocr.Reader(['de', 'en'], gpu=False)
        logger.info(f"Model loaded in {time.time() - start:.1f}s")

    def load_wordlist(self):
        """Load wordlist for quality comparison."""
        if WORDLIST_PATH.exists():
            self.wordlist = set(WORDLIST_PATH.read_text().strip().split('\n'))
            logger.info(f"Loaded wordlist with {len(self.wordlist)} words")
        else:
            logger.warning(f"Wordlist not found at {WORDLIST_PATH}")

    def compute_quality(self, text: str) -> float:
        """Compute text quality score based on wordlist matching."""
        if not self.wordlist or not text:
            return 0.5

        words = text.lower().split()
        # Only check words with 3+ letters that are pure alpha
        checkable = [w.strip('.,;:!?()[]{}"\'-') for w in words if len(w) >= 3]
        checkable = [w for w in checkable if w.isalpha()]

        if len(checkable) < 3:
            return 0.5  # Not enough words to judge

        recognized = sum(1 for w in checkable if w in self.wordlist)
        return recognized / len(checkable)

    def process_request(self, request_file: Path) -> bool:
        """Process a single OCR request."""
        job_id = request_file.stem

        try:
            # Read request metadata
            request_data = json.loads(request_file.read_text())
            image_filename = request_data.get("image_path", f"{job_id}.png")
            image_path = OCR_EXCHANGE_DIR / image_filename

            if not image_path.exists():
                raise FileNotFoundError(f"Image not found: {image_path}")

            logger.info(f"Processing OCR: {image_filename}")
            start = time.time()

            # Run OCR
            results = self.reader.readtext(str(image_path))

            # Extract text and compute confidence
            texts = []
            confidences = []
            for bbox, text, conf in results:
                texts.append(text)
                confidences.append(conf)

            full_text = '\n'.join(texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            quality = self.compute_quality(full_text)

            # Write result
            result_data = {
                "text": full_text,
                "confidence": round(avg_confidence, 3),
                "quality": round(quality, 3),
                "word_count": len(full_text.split()),
                "char_count": len(full_text),
            }
            result_file = OCR_EXCHANGE_DIR / f"{job_id}.result"
            result_file.write_text(json.dumps(result_data, ensure_ascii=False))

            elapsed = time.time() - start
            logger.info(f"OCR complete: {job_id} - {len(full_text)} chars, "
                       f"quality={quality:.2f}, conf={avg_confidence:.2f}, {elapsed:.1f}s")

            # Cleanup request file (processor will cleanup image and result)
            request_file.unlink(missing_ok=True)
            return True

        except Exception as e:
            logger.error(f"OCR failed for {job_id}: {e}", exc_info=True)
            # Write failure marker
            failed_file = OCR_EXCHANGE_DIR / f"{job_id}.failed"
            failed_file.write_text(str(e)[:500])
            request_file.unlink(missing_ok=True)
            return False

    def run(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        logger.info("OCR Watcher starting")
        logger.info(f"Exchange directory: {OCR_EXCHANGE_DIR}")

        # Load model and wordlist at startup (ONCE)
        self.load_model()
        self.load_wordlist()

        logger.info("Ready for OCR requests")

        while self.running:
            # Find pending requests
            request_files = list(OCR_EXCHANGE_DIR.glob("*.request"))

            if request_files:
                for request_file in request_files:
                    if not self.running:
                        break
                    self.process_request(request_file)
            else:
                time.sleep(0.5)

        logger.info("OCR Watcher stopped")


def main():
    watcher = OCRWatcher()
    watcher.run()


if __name__ == "__main__":
    main()
