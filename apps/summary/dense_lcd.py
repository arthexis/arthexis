from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.features.utils import is_suite_feature_enabled
from apps.nodes.roles import node_is_control

from .constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from .services import (
    LCD_SUMMARY_EXPIRES_AFTER,
    compact_log_line,
    execute_log_summary_generation,
    get_summary_config,
)

DENSE_LCD_LOCK_NAME = "lcd-low"
DENSE_LCD_FRAME_COUNT = 6
PROMPT_LOGS_MARKER = "LOGS:"
SOURCE_RE = re.compile(r"^\[(?P<source>[^\]]+)\]$")
ERROR_RE = re.compile(
    r"\b(?:ERR|ERROR|CRITICAL|fail(?:ed|ure)?|exception|panic)\b", re.I
)
WARNING_RE = re.compile(r"\b(?:WRN|WARN|WARNING|blocked|retry|timeout)\b", re.I)


def _prompt_log_lines(prompt: str) -> list[str]:
    _head, separator, tail = str(prompt or "").partition(PROMPT_LOGS_MARKER)
    if not separator:
        return []
    return [line.strip() for line in tail.splitlines() if line.strip()]


def _severity(line: str) -> str:
    if ERROR_RE.search(line):
        return "ERROR"
    if WARNING_RE.search(line):
        return "WARNING"
    return "NORMAL"


def _source_label(source: str) -> str:
    if not source:
        return "logs"
    stem = Path(source).stem or source
    return stem[:10]


def dense_frames_from_prompt(prompt: str) -> list[tuple[str, str]]:
    """Return compact LCD frames derived from the prompt logs."""

    frames: list[tuple[str, str]] = []
    counts: Counter[str] = Counter()
    source = ""
    for raw_line in _prompt_log_lines(prompt):
        source_match = SOURCE_RE.match(raw_line)
        if source_match:
            source = source_match.group("source")
            continue
        line = compact_log_line(raw_line)
        if not line:
            continue
        severity = _severity(line)
        counts[severity] += 1
        if len(frames) >= DENSE_LCD_FRAME_COUNT:
            continue
        prefix = (
            "ERR" if severity == "ERROR" else "WRN" if severity == "WARNING" else "OK"
        )
        frames.append((f"{prefix} {_source_label(source)}", line[:16]))

    if not counts:
        return []

    summary = f"{sum(counts.values())} ln"
    state = (
        f"ERR {counts['ERROR']}"
        if counts["ERROR"]
        else f"WRN {counts['WARNING']}"
        if counts["WARNING"]
        else "NORMAL"
    )
    return [(summary, state), *frames[: DENSE_LCD_FRAME_COUNT - 1]]


def execute_dense_lcd_summary(*, ignore_suite_feature_gate: bool = False) -> str:
    """Generate log summaries and write dense frames to the low LCD channel."""

    from apps.nodes.models import Node
    from apps.tasks.tasks import _write_lcd_frames

    node = Node.get_local()
    if not node:
        return "skipped:no-node"
    if not node_is_control(node):
        return "skipped:non-control-node"
    if not ignore_suite_feature_gate and not is_suite_feature_enabled(
        LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True
    ):
        return "skipped:suite-feature-disabled"
    if not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"

    run_status = execute_log_summary_generation(
        ignore_suite_feature_gate=ignore_suite_feature_gate,
    )
    config = get_summary_config()
    frames = dense_frames_from_prompt(config.last_prompt)
    if not frames:
        return run_status

    lock_file = Path(settings.BASE_DIR) / ".locks" / DENSE_LCD_LOCK_NAME
    if node.has_feature("lcd-screen"):
        _write_lcd_frames(
            frames,
            lock_file=lock_file,
            expires_at=timezone.now() + LCD_SUMMARY_EXPIRES_AFTER,
        )
    elif lock_file.parent.exists():
        _write_lcd_frames([], lock_file=lock_file)
    return f"{run_status};dense:{len(frames)}"


__all__ = [
    "DENSE_LCD_LOCK_NAME",
    "dense_frames_from_prompt",
    "execute_dense_lcd_summary",
]
