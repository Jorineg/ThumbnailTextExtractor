FROM jorineg/ibhelm-base:latest

# Install poppler for pdf2image and docker CLI for QCAD sidecar
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    docker-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data/temp /app/logs

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=thumbnailtextextractor

CMD ["python", "-m", "src.app"]
