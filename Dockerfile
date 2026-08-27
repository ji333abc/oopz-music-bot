FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OOPZ_VOICE_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    QQBOT_JM_UPLOADER=/app/tools/qqbot-uploader/uploader.mjs \
    QQBOT_JM_TEMP_ROOT=/app/data/jm-tasks \
    QQBOT_JM_TIMING_PATH=/app/data/jm_timing.json \
    OOPZ_LEGACY_DATA_DIR=/app/data/legacy \
    OOPZ_LEGACY_SOURCE_ROOT=/app/legacy_oopzbot \
    BOT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    BOT_CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PYTHONPATH=/app/legacy_oopzbot:/app/legacy_oopzbot/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY oopzbot ./oopzbot
COPY legacy_oopzbot ./legacy_oopzbot
COPY tools/qqbot-uploader ./tools/qqbot-uploader

RUN pip install --no-cache-dir ".[jm,legacy]" \
    && npm ci --omit=dev --prefix tools/qqbot-uploader \
    && groupadd --system oopzbot \
    && useradd --system --gid oopzbot --home-dir /app oopzbot \
    && mkdir -p /app/data \
    && chown -R oopzbot:oopzbot /app

USER oopzbot

VOLUME ["/app/data"]
CMD ["oopzbot", "start"]
