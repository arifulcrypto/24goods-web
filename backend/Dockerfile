# syntax=docker/dockerfile:1
#
# One image, several roles (pick with the command):
#   python -m app.serve               public API :8000 + internal listener :8081 (default)
#   python -m app.delivery            outbox consumer + internal reveal API :8001
#   python -m app.devtools.mock_zpay  ZPay simulator :8002 (refuses APP_ENV=prod)
#   alembic upgrade head              migrations (as the schema owner)
#   python -m app.devtools.seed       idempotent demo data
#   python -m app.devtools.keygen --out-dir /keys

# ---- build stage: produce wheels only ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
RUN pip wheel --wheel-dir /wheels .

# ---- runtime stage: no build tooling, no pip, non-root ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_MMAP_THRESHOLD_=131072 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    APP_HOST=0.0.0.0 \
    APP_UPLOAD_DIR=/data/uploads \
    APP_HEALTH_PORT=8000

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads \
    && chown app:app /data/uploads \
    && chmod 750 /data/uploads

COPY --from=build /wheels /tmp/wheels
RUN pip install --no-index --find-links /tmp/wheels goods24-backend \
    && rm -rf /tmp/wheels \
    && pip uninstall -y setuptools wheel pip || true

WORKDIR /app
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root migrations ./migrations
USER app
VOLUME ["/data/uploads"]
EXPOSE 8000 8081 8001 8002
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import os,sys,urllib.request; p=os.environ.get('APP_HEALTH_PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/healthz', timeout=2).status==200 else 1)"]
CMD ["python", "-m", "app.serve"]
