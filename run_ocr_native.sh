#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

VENV_DIR=".venv-ocr"
EXCHANGE_DIR="./ocr-exchange"
WORDLIST_PATH="$VENV_DIR/wordlists/combined.txt"
INSTANCES=${1:-1}

mkdir -p "$EXCHANGE_DIR"

# Setup venv + deps on first run
if [ ! -d "$VENV_DIR" ]; then
    PYTHON=$(command -v python3.12 || command -v python3.13 || command -v python3.11)
    if [ -z "$PYTHON" ] || [ "$($PYTHON -c 'import sys; print(sys.version_info >= (3,10))')" != "True" ]; then
        echo "Error: Python 3.10+ required for MPS support. Install with: brew install python@3.12"
        exit 1
    fi
    echo "Creating Python venv with $PYTHON..."
    "$PYTHON" -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    echo "Installing PyTorch with MPS support..."
    pip install --no-cache-dir torch torchvision

    echo "Installing EasyOCR..."
    pip install --no-cache-dir easyocr numpy Pillow opencv-python-headless

    echo "Patching easyocr corrupt_msg bug..."
    EASYOCR_PY="$VENV_DIR/lib/python*/site-packages/easyocr/easyocr.py"
    # corrupt_msg is used before definition — inject it at the top of __init__
    sed -i.bak "s/model_path = os.path.join(self.model_storage_directory, model\['filename'\])/corrupt_msg = 'MD5 hash mismatch, possible file corruption'\n            model_path = os.path.join(self.model_storage_directory, model['filename'])/" $EASYOCR_PY

    echo "Pre-downloading OCR models..."
    python -c "import easyocr; easyocr.Reader(['de', 'en'], gpu=True, download_enabled=True)"

    echo "Generating wordlist..."
    pip install --no-cache-dir wordfreq
    mkdir -p "$VENV_DIR/wordlists"
    python -c "
from wordfreq import top_n_list
words = set(top_n_list('de', 50000)) | set(top_n_list('en', 50000))
open('$WORDLIST_PATH', 'w').write('\n'.join(sorted(words)))
print(f'Wordlist: {len(words)} words')
"
    pip uninstall -y wordfreq regex langcodes language_data marisa-trie ftfy msgpack wcwidth 2>/dev/null || true
else
    source "$VENV_DIR/bin/activate"
fi

export PYTHONUNBUFFERED=1
export OCR_EXCHANGE_DIR="$EXCHANGE_DIR"
export WORDLIST_PATH="$WORDLIST_PATH"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export MAX_IMAGE_DIMENSION="${MAX_IMAGE_DIMENSION:-4000}"

echo "Starting $INSTANCES native OCR watcher(s) with MPS..."

pids=()
for i in $(seq 1 "$INSTANCES"); do
    python -m src.ocr_watcher &
    pids+=($!)
    echo "  Started OCR watcher $i (PID ${pids[-1]})"
done

cleanup() {
    echo "Stopping OCR watchers..."
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait
}
trap cleanup SIGINT SIGTERM

wait
