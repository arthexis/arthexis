# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PORT=8888

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PORT=8888 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser

COPY --from=builder /opt/venv /opt/venv

COPY --chown=appuser:appuser . .

RUN chmod +x scripts/docker-entrypoint.sh \
    && mkdir -p /app/.locks /app/logs /app/media \
    && chown -R appuser:appuser /app/.locks /app/logs /app/media

USER appuser

EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; admin_path=os.getenv('ADMIN_URL_PATH', 'admin/').strip('/'); health_path=f'/{admin_path}/login/' if admin_path else '/admin/login/'; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8888\")}{health_path}', timeout=3)"

ENTRYPOINT ["scripts/docker-entrypoint.sh"]
