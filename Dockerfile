FROM python:3.12-slim AS source

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OOPZ_VOICE_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    OOPZ_LEGACY_DATA_DIR=/app/data/legacy \
    OOPZ_LEGACY_SOURCE_ROOT=/app/legacy_oopzbot \
    BOT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    BOT_CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PYTHONPATH=/app/legacy_oopzbot:/app/legacy_oopzbot/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY oopzbot ./oopzbot
COPY legacy_oopzbot ./legacy_oopzbot
COPY docker-entrypoint.sh /usr/local/bin/oopzbot-entrypoint

RUN groupadd --system oopzbot \
    && useradd --system --gid oopzbot --home-dir /app oopzbot \
    && mkdir -p /app/data \
    && chown -R oopzbot:oopzbot /app \
    && chmod 0755 /usr/local/bin/oopzbot-entrypoint

VOLUME ["/app/data"]
ENTRYPOINT ["/usr/local/bin/oopzbot-entrypoint"]

FROM source AS core
RUN pip install --no-cache-dir ".[legacy,qqmusic-login]"
CMD ["oopzbot", "start"]

FROM source AS jm-worker
ENV QQBOT_JM_UPLOADER=/app/tools/qqbot-uploader/uploader.mjs \
    QQBOT_JM_TEMP_ROOT=/app/data/jm-tasks
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY tools/qqbot-uploader ./tools/qqbot-uploader
RUN pip install --no-cache-dir ".[jm]" \
    && npm ci --omit=dev --prefix tools/qqbot-uploader \
    && chown -R oopzbot:oopzbot /app
CMD ["oopzbot-jm-service"]

FROM core AS final
