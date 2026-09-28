# DownloaderBot, built from the root of the repository -- not from this folder,
# because the modules every bot shares live beside it, in shared/:
#
#   docker build -f downloader_bot/Dockerfile .
#
# On Railway: the service's Settings -> Config-as-code -> /downloader_bot/railway.json,
# which names this file, and rebuilds only when downloader_bot/ or shared/ changes.
FROM python:3.11-slim-bookworm
# ffmpeg is a system program this bot shells out to.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
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
    LOG_TO_FILES=0

WORKDIR /app
COPY downloader_bot/requirements.txt downloader_bot/requirements.txt
RUN pip install -r downloader_bot/requirements.txt

COPY shared shared
COPY downloader_bot downloader_bot
# Every module this bot has, its own and shared/'s, imports -- or the image is
# never deployed. ENV_FILES=off: an image never carries a .env to read anyway.
RUN python -m compileall -q shared downloader_bot \
 && cd downloader_bot && ENV_FILES=off python -c "import bot" \
 && useradd --create-home --uid 10001 bot && chown -R bot /app
USER bot

WORKDIR /app/downloader_bot
CMD ["python", "-u", "bot.py"]
