FROM jorineg/ibhelm-base:latest

# Install poppler for pdf2image and fuse for AppImage
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    wget \
    fuse \
    && rm -rf /var/lib/apt/lists/*

# Install ODA File Converter (AppImage - portable, no dependencies)
# Download URL changes with versions - update as needed from https://www.opendesign.com/guestfiles/oda_file_converter
ARG ODA_VERSION=25.12
ARG ODA_URL=https://download.opendesign.com/guestfiles/ODAFileConverter/ODAFileConverter_QT6_lnxX64_8.3dll_${ODA_VERSION}.AppImage
RUN wget -q ${ODA_URL} -O /usr/local/bin/ODAFileConverter \
    && chmod +x /usr/local/bin/ODAFileConverter

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data/temp /app/logs

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=thumbnailtextextractor
ENV QT_QPA_PLATFORM=offscreen
ENV APPIMAGE_EXTRACT_AND_RUN=1

CMD ["python", "-m", "src.app"]
