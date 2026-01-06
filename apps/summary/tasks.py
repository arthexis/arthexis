from __future__ import annotations

import logging
import math
import re
import textwrap
from pathlib import Path
from typing import Iterable

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.core.notifications import NotificationManager
from apps.loggers.paths import select_log_dir
from apps.nodes.models import Node
from apps.screens.startup_notifications import lcd_feature_enabled, lcd_feature_enabled_for_paths

from .models import SummaryState

logger = logging.getLogger(__name__)

SUMMARY_CHANNEL = "low-1"
SCREEN_MIN = 8
SCREEN_MAX = 10
ROW_WIDTH = 16
ROW_PLACEHOLDER = "…"

TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?\s+")
LEVEL_SHORTCUTS = (
    ("CRITICAL", "CRT"),
    ("ERROR", "ERR"),
    ("WARNING", "WRN"),
    ("INFO", "INF"),
)


class LocalSummaryModel:
    """Lightweight local summarizer placeholder for LCD output."""

    def summarize(self, *, prompt: str, compacted_logs: list[str]) -> list[tuple[str, str]]:
        prioritized = _prioritize(compacted_logs)
        if not prioritized:
            return [("Logs steady", "No new events")]

        line_budget = _line_budget(len(prioritized))
        selected = prioritized[:line_budget]
        if len(selected) < line_budget:
            selected.extend(["…"] * (line_budget - len(selected)))

        trimmed = [_trim_line(line) for line in selected]
        pairs: list[tuple[str, str]] = []
        for index in range(0, len(trimmed), 2):
            subject = trimmed[index]
            body = trimmed[index + 1] if index + 1 < len(trimmed) else ""
            pairs.append((subject, body))
        return pairs


def _line_budget(line_count: int) -> int:
    screens = max(SCREEN_MIN, min(SCREEN_MAX, math.ceil(line_count / 2)))
    return screens * 2


def _trim_line(text: str) -> str:
    return textwrap.shorten(text.strip(), width=ROW_WIDTH, placeholder=ROW_PLACEHOLDER)


def _prioritize(lines: list[str]) -> list[str]:
    priority: list[str] = []
    normal: list[str] = []
    for line in lines:
        bucket = priority if _is_priority(line) else normal
        bucket.append(line)
    ordered = priority + normal
    return ordered


def _is_priority(line: str) -> bool:
    normalized = line.lower()
    return any(token in normalized for token in ("err", "fail", "warn", "crit"))


def _candidate_log_files(log_dir: Path) -> list[Path]:
    names: set[str] = set()
    base_name = Path(getattr(settings, "LOG_FILE_NAME", ""))
    if base_name.name:
        names.add(base_name.name)
    names.update({"celery.log", "error.log", "page_misses.log"})
    for entry in log_dir.glob("*.log"):
        names.add(entry.name)
    return [log_dir / name for name in sorted(names) if (log_dir / name).exists()]


def _read_incremental_log(path: Path, previous_offset: int) -> tuple[list[str], int]:
    try:
        current_size = path.stat().st_size
    except OSError:
        return [], previous_offset

    offset = previous_offset if previous_offset >= 0 else 0
    if current_size < offset:
        offset = 0

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(offset)
            content = handle.read()
            new_offset = handle.tell()
    except OSError:
        return [], offset

    if not content:
        return [], new_offset

    prefixed = [f"[{path.stem}] {line}" for line in content.splitlines() if line.strip()]
    return prefixed, new_offset


def _compact_lines(lines: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        text = TIMESTAMP_PREFIX.sub("", text)
        for long, short in LEVEL_SHORTCUTS:
            text = text.replace(long, short)
        text = re.sub(r"\s+", " ", text)
        cleaned.append(text)

    return _collapse_repeats(cleaned)


def _collapse_repeats(lines: list[str]) -> list[str]:
    if not lines:
        return []

    collapsed: list[str] = []
    current = lines[0]
    count = 1
    for line in lines[1:]:
        if line == current:
            count += 1
            continue
        collapsed.append(f"{current} x{count}" if count > 1 else current)
        current = line
        count = 1
    collapsed.append(f"{current} x{count}" if count > 1 else current)
    return collapsed


def _build_prompt(compacted_logs: list[str]) -> str:
    guidance = (
        "Summarize key suite events for an operator. Emphasize actionable issues, "
        "timeouts, and recoveries. Respond as LCD-ready subject/body pairs using "
        "abbreviations, ascii symbols, or tiny drawings to fit 14-18 chars per row. "
        "Prepare 8-10 screens, 2 rows each, minimizing scrolling. Keep tone terse."
    )
    context = "\n".join(compacted_logs)
    return f"{guidance}\n\nLogs:\n{context}"


def _get_lock_dir() -> Path:
    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    return base_dir / ".locks"


def _log_state(state: SummaryState, log_dir: Path) -> tuple[list[str], dict[str, int]]:
    offsets = state.log_offsets or {}
    collected: list[str] = []
    new_offsets: dict[str, int] = {}

    for log_file in _candidate_log_files(log_dir):
        previous_offset = int(offsets.get(log_file.name, 0) or 0)
        lines, updated_offset = _read_incremental_log(log_file, previous_offset)
        if lines:
            collected.extend(lines)
        new_offsets[log_file.name] = updated_offset

    return collected, new_offsets


def _schedule_segments(segments: list[tuple[str, str]]) -> None:
    for index, (subject, body) in enumerate(segments):
        countdown = index * 30
        write_summary_segment.apply_async((subject, body), countdown=countdown)


@shared_task(
    name="summary.tasks.write_summary_segment",
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def write_summary_segment(subject: str, body: str) -> str:
    lock_dir = _get_lock_dir()
    if not lcd_feature_enabled(lock_dir):
        return "skipped:lcd-disabled"

    notification = NotificationManager(lock_dir=lock_dir)
    notification.send(subject, body, channel_type=SUMMARY_CHANNEL)
    return f"written:{SUMMARY_CHANNEL}"


@shared_task(
    name="summary.tasks.generate_log_summary",
    soft_time_limit=240,
    time_limit=300,
)
def generate_log_summary() -> str:
    node = Node.get_local()
    if not node or not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"

    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    base_path = node.get_base_path()
    if not lcd_feature_enabled_for_paths(base_dir, base_path):
        return "skipped:lcd-disabled"

    log_dir = Path(getattr(settings, "LOG_DIR", select_log_dir(base_dir)))
    log_dir.mkdir(parents=True, exist_ok=True)

    state = SummaryState.get_default()
    raw_lines, offsets = _log_state(state, log_dir)
    state.log_offsets = offsets
    state.last_run_at = timezone.now()
    state.save(update_fields=["log_offsets", "last_run_at"])

    if not raw_lines:
        return "skipped:no-updates"

    compacted = _compact_lines(raw_lines)
    prompt = _build_prompt(compacted)

    model = LocalSummaryModel()
    segments = model.summarize(prompt=prompt, compacted_logs=compacted)
    _schedule_segments(segments)

    logger.debug("Queued %s LCD summary segments", len(segments))
    return f"queued:{len(segments)}"
