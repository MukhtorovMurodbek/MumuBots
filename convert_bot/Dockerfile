# ConvertBot, built from the root of the repository -- not from this folder,
# because the modules every bot shares live beside it, in shared/:
#
#   docker build -f convert_bot/Dockerfile .
#
# On Railway: the service's Settings -> Config-as-code -> /convert_bot/railway.json,
# which names this file, and rebuilds only when convert_bot/ or shared/ changes.
FROM python:3.11-slim-bookworm
# ffmpeg and bsdtar (libarchive's tar: it reads 7z and RAR and writes 7z) are
# system programs this bot shells out to. Without bsdtar the bot starts and
# simply does not offer 7z or RAR -- formats.probe() notices.
# The four tesseract-ocr-* packages are language DATA for reading text from
# pictures and scanned PDFs (English, Russian, Uzbek in Latin and Cyrillic). The
# engine itself is inside PyMuPDF, so --no-install-recommends matters here: it
# keeps these from pulling in the tesseract program, which nothing uses.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libarchive-tools \
    tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-uzb tesseract-ocr-uzb-cyrl \
 && rm -rf /var/lib/apt/lists/*

# The running-cost settings the Nixpacks build had in nixpacks.toml: glibc's
# malloc arenas capped (resident memory is what the host bills), no pip cache
# in the image, unbuffered logs, no .pyc for a filesystem rebuilt on every
# deploy. LOG_TO_FILES=0: a container's log is its stdout.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    LOG_TO_FILES=0 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app
COPY convert_bot/requirements.txt convert_bot/requirements.txt
RUN pip install -r convert_bot/requirements.txt

COPY shared shared
COPY convert_bot convert_bot
# Every module this bot has, its own and shared/'s, imports -- or the image is
# never deployed. ENV_FILES=off: an image never carries a .env to read anyway.
# The self-test reads a line of English and a line of Russian from a page it
# draws, so an image that cannot read text (no language data, a PyMuPDF
# without the engine) fails here and not on somebody's scan. PYTHONPATH: it is
# not bot.py, which finds shared/ for itself, that is being imported.
RUN python -m compileall -q shared convert_bot \
 && cd convert_bot && ENV_FILES=off python -c "import bot" \
 && ENV_FILES=off PYTHONPATH=../shared python -c "import ocr_convert; ocr_convert.selftest()" \
 && useradd --create-home --uid 10001 bot && chown -R bot /app
USER bot

WORKDIR /app/convert_bot
CMD ["python", "-u", "bot.py"]
