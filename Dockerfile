FROM jorineg/ibhelm-base:latest

# Install poppler for pdf2image and dependencies for ODA File Converter
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libxcb-cursor0 \
    libxcb-xinerama0 \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 || true

# Install ODA File Converter (DEB package)
# Download manually from https://www.opendesign.com/guestfiles/oda_file_converter
COPY ODAFileConverter_QT6_lnxX64_8.3dll_26.10.deb /tmp/oda.deb
RUN dpkg -i /tmp/oda.deb || apt-get install -f -y \
    && rm /tmp/oda.deb

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data/temp /app/logs

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=thumbnailtextextractor
ENV QT_QPA_PLATFORM=offscreen

CMD ["python", "-m", "src.app"]
