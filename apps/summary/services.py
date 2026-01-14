from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
import re
import textwrap
from typing import Iterable

from django.conf import settings
from django.utils import timezone

from apps.screens.startup_notifications import render_lcd_lock_file

from .models import LLMSummaryConfig

logger = logging.getLogger(__name__)

LCD_COLUMNS = 16
DEFAULT_MODEL_DIR = Path(settings.BASE_DIR) / "work" / "llm" / "lcd-summary"
DEFAULT_MODEL_FILE = "MODEL.README"

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?\s+"
)
LEVEL_RE = re.compile(r"\b(INFO|DEBUG|WARNING|ERROR|CRITICAL)\b")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class LogChunk:
    path: Path
    content: str


def get_summary_config() -> LLMSummaryConfig:
    config, _created = LLMSummaryConfig.objects.get_or_create(
        slug="lcd-log-summary",
        defaults={"display": "LCD Log Summary"},
    )
    return config


def _resolve_model_path(config: LLMSummaryConfig) -> Path:
    if config.model_path:
        return Path(config.model_path)
    env_override = os.getenv("ARTHEXIS_LLM_SUMMARY_MODEL")
    if env_override:
        return Path(env_override)
    return DEFAULT_MODEL_DIR


def resolve_model_path(config: LLMSummaryConfig) -> Path:
    return _resolve_model_path(config)


def ensure_local_model(config: LLMSummaryConfig) -> Path:
    model_dir = _resolve_model_path(config)
    model_dir.mkdir(parents=True, exist_ok=True)
    sentinel = model_dir / DEFAULT_MODEL_FILE
    if not sentinel.exists():
        sentinel.write_text(
            "Local LCD summary model placeholder. Replace with actual model files.\n",
            encoding="utf-8",
        )
    config.model_path = str(model_dir)
    config.mark_installed()
    return model_dir


def _safe_offset(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def collect_recent_logs(
    config: LLMSummaryConfig,
    *,
    since: datetime,
    log_dir: Path | None = None,
) -> list[LogChunk]:
    if log_dir is None:
        log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    offsets = dict(config.log_offsets or {})
    chunks: list[LogChunk] = []

    if not log_dir.exists():
        logger.warning("Log directory missing: %s", log_dir)
        return []

    candidates = sorted(log_dir.rglob("*.log"))
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        since_ts = since.timestamp()
        if stat.st_mtime < since_ts:
            continue
        offset = _safe_offset(offsets.get(str(path)))
        size = stat.st_size
        if offset > size:
            offset = 0
        if size <= offset:
            offsets[str(path)] = size
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                content = handle.read()
        except OSError:
            continue
        if content:
            chunks.append(LogChunk(path=path, content=content))
        offsets[str(path)] = size

    config.log_offsets = offsets
    return chunks


def compact_log_line(line: str) -> str:
    cleaned = TIMESTAMP_RE.sub("", line)
    cleaned = UUID_RE.sub("<uuid>", cleaned)
    cleaned = HEX_RE.sub("<hex>", cleaned)
    cleaned = IP_RE.sub("<ip>", cleaned)
    cleaned = LEVEL_RE.sub(lambda match: match.group(1)[:3], cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def compact_log_chunks(chunks: Iterable[LogChunk]) -> str:
    compacted: list[str] = []
    for chunk in chunks:
        header = f"[{chunk.path.name}]"
        compacted.append(header)
        for line in chunk.content.splitlines():
            trimmed = compact_log_line(line)
            if trimmed:
                compacted.append(trimmed)
    return "\n".join(compacted)


def build_summary_prompt(compacted_logs: str, *, now: datetime) -> str:
    cutoff = (now - timedelta(minutes=4)).strftime("%H:%M")
    instructions = textwrap.dedent(
        f"""
        You summarize system logs for a 16x2 LCD. Focus on the last 4 minutes (cutoff {cutoff}).
        Highlight urgent operator actions or failures. Use shorthand, abbreviations, and ASCII symbols.
        Output 8-10 LCD screens. Each screen is two lines (subject then body).
        Aim for 14-18 chars per line, avoid scrolling when possible.
        Format:
        SCREEN 1:
        <subject line>
        <body line>
        ---
        SCREEN 2:
        <subject line>
        <body line>
        ...
        Only output the screens, no extra commentary.
        """
    ).strip()
    return f"{instructions}\n\nLOGS:\n{compacted_logs}\n"


def parse_screens(output: str) -> list[tuple[str, str]]:
    if not output:
        return []
    cleaned = [line.rstrip() for line in output.splitlines()]
    groups: list[list[str]] = []
    current: list[str] = []
    for line in cleaned:
        if not line.strip():
            continue
        if line.strip() == "---":
            if current:
                groups.append(current)
                current = []
            continue
        if line.lower().startswith("screen"):
            continue
        current.append(line)
    if current:
        groups.append(current)

    screens: list[tuple[str, str]] = []
    for group in groups:
        if len(group) < 2:
            continue
        screens.append((group[0], group[1]))
    return screens


def _normalize_line(text: str) -> str:
    normalized = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    normalized = normalized.strip()
    if len(normalized) <= LCD_COLUMNS:
        return normalized.ljust(LCD_COLUMNS)
    trimmed = normalized[: LCD_COLUMNS - 3].rstrip()
    return f"{trimmed}...".ljust(LCD_COLUMNS)


def normalize_screens(screens: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for subject, body in screens:
        normalized.append((_normalize_line(subject), _normalize_line(body)))
    return normalized


def render_lcd_payload(subject: str, body: str) -> str:
    return render_lcd_lock_file(subject=subject, body=body)
