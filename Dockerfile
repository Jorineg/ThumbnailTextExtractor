# Stage 1: Get LibreDWG binaries
FROM kuzoncby/libredwg:latest AS libredwg

# Stage 2: Main image
FROM jorineg/ibhelm-base:latest

# Install poppler for pdf2image, ImageMagick for SVG conversion, musl for LibreDWG
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    imagemagick \
    librsvg2-bin \
    musl \
    && rm -rf /var/lib/apt/lists/*

# Copy LibreDWG binaries from first stage
COPY --from=libredwg /usr/local/bin/dwg* /usr/local/bin/
COPY --from=libredwg /usr/local/lib/libredwg* /usr/local/lib/
RUN ldconfig

ENV LD_LIBRARY_PATH=/usr/local/lib

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data/temp /app/logs

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=thumbnailtextextractor

CMD ["python", "-m", "src.app"]
