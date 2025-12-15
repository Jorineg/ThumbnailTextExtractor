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
# Download URL changes with versions - update as needed from https://www.opendesign.com/guestfiles/oda_file_converter
ARG ODA_VERSION=25.12
ARG ODA_URL=https://download.opendesign.com/guestfiles/ODAFileConverter/ODAFileConverter_QT6_lnxX64_8.3dll_${ODA_VERSION}.deb
RUN wget -q ${ODA_URL} -O /tmp/oda.deb \
    && dpkg -i /tmp/oda.deb || apt-get install -f -y \
    && rm /tmp/oda.deb

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data/temp /app/logs

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=thumbnailtextextractor
ENV QT_QPA_PLATFORM=offscreen

CMD ["python", "-m", "src.app"]
