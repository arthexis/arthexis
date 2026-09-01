from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from celery import shared_task

from apps.summary.constants import (
    LCD_SUMMARY_OPERATOR_WORDS,
    LCD_SUMMARY_WINDOW_LABEL,
    LLM_SUMMARY_CELERY_TASK_NAME,
)

logger = logging.getLogger(__name__)

LCD_SUMMARY_COLUMNS = 16
SUMMARY_COUNT_METRIC_RE = re.compile(
    r"^(?P<count>\d+)\s*(?P<unit>lines?|lns?|x)\b(?:\s*/\s*(?P<label>\d+\s*[smhd]))?",
    re.IGNORECASE,
)
SUMMARY_CONTEXT_WINDOW_LABEL_RE = re.compile(
    r"^LCD_CONTEXT_WINDOW_LABEL:\s*(?P<label>\S+)\s*$",
    re.MULTILINE,
)


class LocalLLMSummaryError(RuntimeError):
    """Raised when deterministic summary generation cannot produce output."""


class LocalLLMSummarizer:
    """Deterministic local summarizer adapter used for LCD log rotations."""

    def __init__(self, config: object | None = None) -> None:
        self.config = config
        self.last_audit: dict[str, object] = {}

    def summarize(self, prompt: str) -> str:
        started = time.monotonic()
        output = self._fallback(prompt)
        self.last_audit = {
            "mode": "deterministic",
            "status": "ok",
            "prompt_bytes": len(prompt.encode("utf-8")),
            "response_bytes": len(output.encode("utf-8")),
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        return output

    def _fallback(self, prompt: str) -> str:
        window_label = _summary_window_label(prompt)
        log_lines: list[str] = []
        in_logs = False
        for line in prompt.splitlines():
            if line.strip() == "LOGS:":
                in_logs = True
                continue
            if in_logs and line.strip():
                log_lines.append(line)

        event_lines = [
            line
            for line in log_lines
            if not (line.startswith("[") and line.endswith("]"))
        ]
        if not event_lines:
            return _summary_screen(
                "No recent logs",
                _summary_status_line("0 ln", "NORMAL", window_label=window_label),
            )

        attention_events = [
            (idx, line, severity)
            for idx, line in enumerate(event_lines)
            if (severity := _summary_severity(line)) != "OK"
        ]
        error_lines = [
            line for _, line, severity in attention_events if severity == "ERR"
        ]
        warn_lines = [
            line for _, line, severity in attention_events if severity == "WRN"
        ]
        task_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for line in event_lines:
            if _summary_severity(line) != "OK":
                continue
            task_label = _summary_task_label(line)
            if task_label:
                task_counts[task_label] = task_counts.get(task_label, 0) + 1
                continue
            source_label = _summary_source_label(line)
            if source_label:
                source_counts[source_label] = source_counts.get(source_label, 0) + 1

        screens: list[tuple[str, str]] = []
        if error_lines or warn_lines:
            evaluation = _summary_evaluation(len(error_lines), len(warn_lines))
            headline_idx, headline_line, _headline_severity = next(
                event
                for event in reversed(attention_events)
                if event[2] == ("ERR" if error_lines else "WRN")
            )
            screens.append(
                (
                    _summary_compact_line(headline_line),
                    _summary_status_line(
                        f"{len(event_lines)} ln",
                        evaluation,
                        window_label=window_label,
                    ),
                )
            )
        else:
            screens.append(
                (
                    "No err/wrn logs",
                    _summary_status_line(
                        f"{len(event_lines)} ln",
                        "NORMAL",
                        window_label=window_label,
                    ),
                )
            )

        detail_events = (
            [event for event in attention_events if event[0] != headline_idx][-3:]
            if attention_events
            else []
        )
        for _idx, line, severity in detail_events:
            screens.append(
                (
                    _summary_compact_line(line),
                    _summary_status_line(
                        "1 ln",
                        "WARNING" if severity == "WRN" else "ERROR",
                        window_label=window_label,
                    ),
                )
            )

        for label, count in _summary_top_counts(task_counts, limit=4):
            screens.append(
                (
                    label,
                    _summary_status_line(
                        f"{count}x",
                        "NORMAL",
                        window_label=window_label,
                    ),
                )
            )

        if len(screens) < 3:
            for label, count in _summary_top_counts(source_counts, limit=3):
                screens.append(
                    (
                        label,
                        _summary_status_line(
                            f"{count}x",
                            "NORMAL",
                            window_label=window_label,
                        ),
                    )
                )

        if len(screens) == 1:
            if error_lines:
                screens.append(
                    (
                        "Check logs",
                        _summary_status_line("1x", "FIX", window_label=window_label),
                    )
                )
            elif warn_lines:
                screens.append(
                    (
                        "Review logs",
                        _summary_status_line(
                            "1x",
                            "CHECK",
                            window_label=window_label,
                        ),
                    )
                )
            else:
                screens.append(
                    (
                        "Routine",
                        _summary_status_line(
                            "0x",
                            "NORMAL",
                            window_label=window_label,
                        ),
                    )
                )

        return "\n---\n".join(
            _summary_screen(subject, body) for subject, body in screens
        )


SUMMARY_TASK_ALIASES = {
    "apps.core.tasks.heartbeat": "HB OK",
    "apps.ocpp.tasks.send_offline_charge_point_notifications": "OCPP NOTE",
    "apps.repos.tasks.monitor_github_readiness": "GH MON",
    "terminals.ensure_agent_terminals": "TERM CHK",
}

SUMMARY_SOURCE_ALIASES = {
    "apps.core.tasks.heartbeat": "HB",
    "celery.beat": "Beat",
    "celery.worker.strategy": "Worker",
    "celery.app.trace": "Task trace",
    "apps.ocpp": "OCPP",
}

SUMMARY_TASK_RE = re.compile(r"Task ([\w.]+)\[")
SUMMARY_DUE_TASK_RE = re.compile(r"Sending due task [\w-]+ \(([\w.]+)\)")
SUMMARY_LEVEL_PATTERN = (
    r"\[?(?:DBG|DEBUG|INF|INFO|WRN|WAR|WARN|WARNING|ERR|ERROR|CRI|CRITICAL)\]?"
)
SUMMARY_LEVEL_RE = re.compile(rf"^{SUMMARY_LEVEL_PATTERN}\s+")
SUMMARY_SOURCE_RE = re.compile(rf"^{SUMMARY_LEVEL_PATTERN}\s+([\w.]+):")


def _summary_window_label(prompt: str) -> str:
    match = SUMMARY_CONTEXT_WINDOW_LABEL_RE.search(prompt)
    if not match:
        return LCD_SUMMARY_WINDOW_LABEL
    return match.group("label")[:8] or LCD_SUMMARY_WINDOW_LABEL


def _summary_severity(line: str) -> str:
    level_match = SUMMARY_LEVEL_RE.match(line)
    level = level_match.group(0).strip("[] ").upper() if level_match else ""
    if level in {"ERR", "ERROR", "CRI", "CRITICAL"} or " raised unexpected" in line:
        return "ERR"
    if level in {"WRN", "WAR", "WARN", "WARNING"}:
        return "WRN"
    return "OK"


def _summary_evaluation(error_count: int, warn_count: int) -> str:
    if error_count:
        return "ERROR"
    if warn_count:
        return "WARNING"
    return "NORMAL"


def _summary_alias(value: str, aliases: dict[str, str]) -> str:
    for prefix, label in aliases.items():
        if value == prefix or value.startswith(f"{prefix}."):
            return label
    return value.rsplit(".", 1)[-1].replace("_", " ")[:16]


def _summary_task_label(line: str) -> str | None:
    match = SUMMARY_DUE_TASK_RE.search(line) or SUMMARY_TASK_RE.search(line)
    if not match:
        if "Heartbeat task executed" in line:
            return "HB OK"
        return None
    return _summary_alias(match.group(1), SUMMARY_TASK_ALIASES)


def _summary_source_label(line: str) -> str | None:
    match = SUMMARY_SOURCE_RE.match(line)
    if not match:
        return None
    return _summary_alias(match.group(1), SUMMARY_SOURCE_ALIASES)


def _summary_top_counts(counts: dict[str, int], *, limit: int) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _summary_compact_line(line: str) -> str:
    cleaned = SUMMARY_LEVEL_RE.sub("", line)
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
    cleaned = re.sub(r"^[\w.]+:\s+", "", cleaned)
    cleaned = cleaned.replace("Task ", "")
    cleaned = cleaned.replace("raised unexpected:", "raised:")
    cleaned = cleaned.replace("Scheduler: Sending due task", "due")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "-"
    return cleaned[:24]


def _summary_status_line(
    metric: str,
    evaluation: str,
    *,
    window_label: str = LCD_SUMMARY_WINDOW_LABEL,
) -> str:
    left = _summary_metric_text(metric, window_label=window_label)
    raw_right = re.sub(r"\s+", " ", str(evaluation or "")).strip().upper()
    right = LCD_SUMMARY_OPERATOR_WORDS.get(raw_right, raw_right)
    if not left:
        return right[:LCD_SUMMARY_COLUMNS]
    if not right:
        return left[:LCD_SUMMARY_COLUMNS]
    if len(left) + 1 + len(right) > LCD_SUMMARY_COLUMNS:
        available_left = LCD_SUMMARY_COLUMNS - len(right) - 1
        if available_left <= 0:
            return right[:LCD_SUMMARY_COLUMNS]
        return f"{left[:available_left].rstrip()} {right}"[:LCD_SUMMARY_COLUMNS]
    return f"{left}{' ' * (LCD_SUMMARY_COLUMNS - len(left) - len(right))}{right}"


def _summary_metric_text(
    metric: str,
    *,
    window_label: str = LCD_SUMMARY_WINDOW_LABEL,
) -> str:
    left = re.sub(r"\s+", " ", str(metric or "")).strip()
    match = SUMMARY_COUNT_METRIC_RE.match(left)
    if not match:
        return left

    count = match.group("count")
    unit = match.group("unit").lower()
    effective_label = (
        re.sub(r"\s+", "", match.group("label"))
        if match.group("label")
        else window_label
    )
    if unit == "x":
        return f"{count}x/{effective_label}"
    return f"{count} ln/{effective_label}"


def _summary_lcd_line(text: str, *, collapse_whitespace: bool = True) -> str:
    normalized = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in str(text or ""))
    if collapse_whitespace:
        normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()[:LCD_SUMMARY_COLUMNS]


def _summary_screen(subject: str, body: str) -> str:
    line1 = _summary_lcd_line(subject)
    line2 = _summary_lcd_line(body, collapse_whitespace=False)
    return f"{line1}\n{line2}".rstrip()


def _write_lcd_frames(
    frames: list[tuple[str, str]],
    *,
    lock_file: Path,
    expires_at=None,
) -> None:
    """Persist LCD frames into channel lock files."""

    from apps.summary.services import render_lcd_payload

    base_name = lock_file.name
    prefix = f"{base_name}-"

    if not frames:
        lock_file.unlink(missing_ok=True)
        for candidate in lock_file.parent.glob(f"{prefix}*"):
            suffix = candidate.name[len(prefix) :]
            if suffix.isdigit():
                candidate.unlink(missing_ok=True)
        return

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    for idx, (subject, body) in enumerate(frames):
        target = lock_file if idx == 0 else lock_file.with_name(f"{base_name}-{idx}")
        payload = render_lcd_payload(subject, body, expires_at=expires_at)
        target.write_text(payload, encoding="utf-8")

    for candidate in lock_file.parent.glob(f"{prefix}*"):
        suffix = candidate.name[len(prefix) :]
        if not suffix.isdigit() or (0 < int(suffix) < len(frames)):
            continue
        candidate.unlink(missing_ok=True)


@shared_task(name=LLM_SUMMARY_CELERY_TASK_NAME)
def generate_log_summary() -> str:
    from apps.summary.services import execute_log_summary_generation

    return execute_log_summary_generation()


__all__ = [
    "LocalLLMSummarizer",
    "LocalLLMSummaryError",
    "_write_lcd_frames",
    "generate_log_summary",
]
