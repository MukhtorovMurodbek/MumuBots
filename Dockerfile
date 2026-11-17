FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libarchive-tools tini \
    tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-uzb tesseract-ocr-uzb-cyrl \
    libreoffice-impress-nogui libreoffice-writer-nogui libreoffice-calc-nogui \
    fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 fonts-dejavu-core fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    LOG_TO_FILES=0 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    MUMU_CHECK_OFFICE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN python -m compileall -q main.py bots shared \
 && python main.py --check \
 && useradd --create-home --uid 10001 bots \
 && mkdir -p data && chown -R bots /app/data \
 && chmod 755 /app/start.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/app/start.sh"]
CMD ["python", "main.py"]
