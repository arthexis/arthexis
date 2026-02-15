FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        gcc \
        git \
        graphviz \
        libffi-dev \
        libgl1 \
        libjpeg62-turbo \
        libpq-dev \
        libssl-dev \
        redis-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements*.txt pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["/app/scripts/ci/run.sh"]
