FROM jorineg/ibhelm-base:latest

# Install poppler for pdf2image and dependencies for ODA File Converter
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    wget \
    libxcb-cursor0 \
    libxcb-xinerama0 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 || true

# Install ODA File Converter (DEB package)
# Download URL from https://www.opendesign.com/guestfiles/oda_file_converter
ARG ODA_FILENAME=ODAFileConverter_QT6_lnxX64_8.3dll_26.10.deb
RUN wget -q "https://www.opendesign.com/guestfiles/get?filename=${ODA_FILENAME}" -O /tmp/oda.deb \
    && dpkg -i /tmp/oda.deb || apt-get install -f -y \
    && rm /tmp/oda.deb \
    && echo "ODA installed to:" && find /opt -name "ODAFileConverter" 2>/dev/null || true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data/temp /app/logs

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=thumbnailtextextractor
ENV QT_QPA_PLATFORM=offscreen

CMD ["python", "-m", "src.app"]
