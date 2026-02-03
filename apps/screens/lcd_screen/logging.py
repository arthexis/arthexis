"""Logging configuration and path helpers for the LCD screen service."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from apps.loggers.rotation import build_daily_rotating_file_handler


def _resolve_base_dir() -> Path:
    env_base = os.getenv("ARTHEXIS_BASE_DIR")
    if env_base:
        return Path(env_base)

    cwd = Path.cwd()
    if (cwd / ".locks").exists():
        return cwd

    return Path(__file__).resolve().parents[2]


BASE_DIR = _resolve_base_dir()
LOGS_DIR = BASE_DIR / "logs"
LOG_FILE = LOGS_DIR / "lcd-screen.log"
WORK_DIR = BASE_DIR / "work"
WORK_FILE = WORK_DIR / "lcd-screen.txt"
HISTORY_DIR = WORK_DIR
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

file_handler = build_daily_rotating_file_handler(
    LOG_FILE,
    formatter=logging.Formatter(LOG_FORMAT),
)
logging.basicConfig(
    level=logging.DEBUG,
    format=LOG_FORMAT,
    handlers=[file_handler, logging.StreamHandler()],
)
root_logger = logging.getLogger()
if not any(
    isinstance(handler, logging.FileHandler)
    and Path(getattr(handler, "baseFilename", "")) == LOG_FILE
    for handler in root_logger.handlers
):
    root_logger.addHandler(file_handler)
root_logger.setLevel(logging.DEBUG)
