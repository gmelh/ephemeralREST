################################################################################
#  ephemeralREST — one image: the API and its web app.                         #
#                                                                              #
#  docker build -t ephemeralrest .                                             #
#  docker run -p 8080:8080 -v ephemeral-data:/data \                           #
#      -e SECRET_KEY=... -e PUBLIC_URL=http://localhost:8080 ephemeralrest     #
#                                                                              #
#  Everything the server keeps is under /data: the database, the Swiss         #
#  Ephemeris files (sweph/), the cities list (cities/), logs and federation    #
#  test files. Inside the container, `ephemeral` runs the admin commands.      #
################################################################################

# ---- The web app ---------------------------------------------------------------
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run typecheck && npm run build

# ---- Python dependencies (pyswisseph needs a compiler) ---------------------------
# pyswisseph has a ready-made wheel for amd64 only; for arm64 it's compiled here. That needs
# the C and C++ compilers (it has one C++ file) and the C library's headers (libc6-dev), which
# --no-install-recommends would otherwise leave out.
FROM python:3.11-slim AS deps
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- The image -------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EPHEMERAL_DATA_DIR=/data \
    EPHEMERAL_NO_ENV_FILE=1 \
    EPHEMERAL_WEB_DIR=/app/web/dist \
    DATABASE_PATH=/data/ephemeral.db \
    SWISS_EPHEMERIS_PATH=/data/sweph \
    CITIES_FOLDER=/data/cities \
    LOG_FILE=/data/logs/ephemeral.log \
    USAGE_COUNT_FILE=/data/api_usage_count.json \
    FEDERATION_CONFIG_DIR=/data/federation \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=8080

# pyswisseph needs the C++ runtime (usually there already; this makes sure).
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --home-dir /app ephemeral
WORKDIR /app

COPY --from=deps /install /usr/local
# Stop the build here, plainly, if a compiled library won't load on this platform.
RUN python -c "import swisseph, mysql.connector, cryptography.fernet; print('swisseph', swisseph.version)"
COPY *.py ./
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
COPY docker/ephemeral /usr/local/bin/ephemeral
COPY --from=web /web/dist ./web/dist
RUN chmod 755 /usr/local/bin/entrypoint /usr/local/bin/ephemeral \
    && mkdir -p /data && chown ephemeral /data

USER ephemeral
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/ping', timeout=4)"

ENTRYPOINT ["entrypoint"]
CMD ["gunicorn", "-c", "gunicorn_config.py", "--preload", "app:app"]