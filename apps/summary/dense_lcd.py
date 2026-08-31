from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.features.utils import is_suite_feature_enabled
from apps.nodes.roles import node_is_control

from .constants import LCD_SUMMARY_WINDOW_LABEL, LLM_SUMMARY_SUITE_FEATURE_SLUG
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
WINDOW_LABEL_RE = re.compile(r"^LCD_CONTEXT_WINDOW_LABEL:\s*(?P<label>\S+)\s*$", re.M)
ERROR_RE = re.compile(
    r"\b(?:ERR|ERROR|CRI|CRITICAL|fail(?:ed|ure)?|exception|panic)\b", re.I
)
WARNING_RE = re.compile(r"\b(?:WRN|WAR|WARN|WARNING|blocked|retry|timeout)\b", re.I)
LOG_LEVEL_PREFIX_RE = re.compile(
    r"^(?:\[(?:DBG|INF|WRN|WAR|WARN|ERR|CRI|DEBUG|INFO|WARNING|ERROR|CRITICAL)\]|"
    r"(?:DBG|INF|WRN|WAR|WARN|ERR|CRI|DEBUG|INFO|WARNING|ERROR|CRITICAL))\s+",
    re.I,
)
LOG_CONTEXT_RE = re.compile(r"\[[^\]]+\]")
LOG_MODULE_PREFIX_RE = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+:\s*")
SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "NORMAL": 2}


@dataclass(frozen=True)
class DenseLogEntry:
    source: str
    severity: str
    text: str


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


def _prompt_window_label(prompt: str) -> str:
    match = WINDOW_LABEL_RE.search(str(prompt or ""))
    if not match:
        return LCD_SUMMARY_WINDOW_LABEL
    return match.group("label")[:8] or LCD_SUMMARY_WINDOW_LABEL


def _source_label(source: str) -> str:
    if not source:
        return "logs"
    text = str(source).strip()
    lower = text.lower()
    if "celery-beat" in lower or "celery.beat" in lower:
        return "beat"
    if "celery" in lower:
        return "celery"
    if "lcd" in lower:
        return "lcd"
    if "usb" in lower:
        return "usb"
    if "rfid" in lower:
        return "rfid"
    if "journal" in lower:
        return "journal"
    if "systemctl" in lower:
        return "systemctl"

    name = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    name = name.rsplit(":", 1)[-1]
    stem = Path(name).stem or name
    label = re.sub(r"[-_]?arthexis$", "", stem, flags=re.I)
    label = label.replace("_", " ").replace("-", " ").strip()
    return (label or "logs")[:10]


def _body_text(line: str) -> str:
    text = LOG_LEVEL_PREFIX_RE.sub("", line)
    text = LOG_CONTEXT_RE.sub("", text)
    text = LOG_MODULE_PREFIX_RE.sub("", text)
    text = text.replace("Scheduler: Sending due task", "due")
    text = text.replace("raised unexpected:", "raised:")
    text = text.replace("Task ", "")
    text = re.sub(r"\s+", " ", text).strip()
    return (text or line)[:16]


def _prompt_entries(prompt: str) -> list[DenseLogEntry]:
    entries: list[DenseLogEntry] = []
    source = ""
    for raw_line in _prompt_log_lines(prompt):
        source_match = SOURCE_RE.match(raw_line)
        if source_match:
            source = source_match.group("source")
            continue
        line = compact_log_line(raw_line)
        if not line:
            continue
        entries.append(
            DenseLogEntry(source=source, severity=_severity(line), text=line)
        )
    return entries


def _entry_frame(entry: DenseLogEntry) -> tuple[str, str]:
    prefix = (
        "ERR"
        if entry.severity == "ERROR"
        else "WRN" if entry.severity == "WARNING" else "OK"
    )
    return (f"{prefix} {_source_label(entry.source)}"[:16], _body_text(entry.text))


def _dedupe_frames(frames: list[tuple[str, str]]) -> list[tuple[str, str]]:
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for subject, body in frames:
        key = (subject.lower(), body.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append((subject, body))
    return unique


def _normal_source_frames(
    entries: list[DenseLogEntry], *, window_label: str
) -> list[tuple[str, str]]:
    counts = Counter(
        _source_label(entry.source) for entry in entries if entry.severity == "NORMAL"
    )
    frames: list[tuple[str, str]] = []
    for source, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        frames.append((f"OK {source}"[:16], f"{count}x/{window_label}"[:16]))
    return frames


def dense_frames_from_prompt(prompt: str) -> list[tuple[str, str]]:
    """Return compact LCD frames derived from the prompt logs."""

    entries = _prompt_entries(prompt)
    if not entries:
        return []

    window_label = _prompt_window_label(prompt)
    counts: Counter[str] = Counter(entry.severity for entry in entries)
    summary = f"{sum(counts.values())} ln/{window_label}"[:16]
    state = (
        f"ERR {counts['ERROR']}"
        if counts["ERROR"]
        else f"WRN {counts['WARNING']}" if counts["WARNING"] else "NORMAL"
    )
    attention_entries = sorted(
        (entry for entry in entries if entry.severity != "NORMAL"),
        key=lambda entry: SEVERITY_ORDER[entry.severity],
    )
    detail_frames = _dedupe_frames([_entry_frame(entry) for entry in attention_entries])
    if len(detail_frames) < DENSE_LCD_FRAME_COUNT - 1:
        detail_frames.extend(
            frame
            for frame in _normal_source_frames(entries, window_label=window_label)
            if frame not in detail_frames
        )
    return [(summary, state), *detail_frames[: DENSE_LCD_FRAME_COUNT - 1]]


def execute_dense_lcd_summary(*, ignore_suite_feature_gate: bool = False) -> str:
    """Generate log summaries and write dense frames to the low LCD channel."""

    from apps.nodes.models import Node
    from apps.summary.tasks import _write_lcd_frames

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
    if not run_status.startswith("wrote:"):
        return run_status

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
