FROM python:3.13.1-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
        libffi-dev \
        libgl1 \
        libglib2.0-0 \
        libmagic1 \
        libpq-dev \
        libssl-dev \
        pkg-config \
        redis-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements*.txt pyproject.toml ./
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

COPY . .

RUN useradd --create-home --shell /bin/bash ciuser \
    && chown -R ciuser:ciuser /app

USER ciuser

ENTRYPOINT ["bash", "scripts/ci/run.sh"]
