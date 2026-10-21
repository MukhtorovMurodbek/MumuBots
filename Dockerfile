# Every bot in one container, one process. Builds the same on Railway,
# Render, Fly.io or a plain `docker build`.
FROM python:3.11-slim-bookworm

# StickerBot, ConvertBot and DownloaderBot shell out to ffmpeg; ConvertBot also to bsdtar
# (libarchive's tar: it reads 7z and RAR and writes 7z -- without it those two are
# simply not offered). The four tesseract-ocr-* packages are language DATA for reading
# text from pictures and scanned PDFs (English, Russian, Uzbek in Latin and Cyrillic);
# the engine is inside PyMuPDF, so --no-install-recommends keeps them from pulling in
# the tesseract program, which nothing uses.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libarchive-tools \
    tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-uzb tesseract-ocr-uzb-cyrl \
 && rm -rf /var/lib/apt/lists/*

# MALLOC_ARENA_MAX keeps glibc from holding freed memory in one arena per
# core; resident memory is what a usage-billed host charges for.
# LOG_TO_FILES=0: a container's log is its stdout; /logs reads from memory.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    LOG_TO_FILES=0 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# The runner's bytecode once, here (the sections are compiled as they load).
# Then every bot is loaded, one conversion is run and a test page's text is read, so
# an image in which a bot cannot load, or cannot read text, never gets as far as a deploy.
RUN python -m compileall -q main.py \
 && python main.py --check \
 && useradd --create-home --uid 10001 bots \
 && mkdir -p data && chown -R bots /app
USER bots

CMD ["python", "main.py"]
