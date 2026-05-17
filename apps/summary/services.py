from __future__ import annotations

import logging
import os
import re
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.features.parameters import get_feature_parameter
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.roles import node_is_control
from apps.screens.startup_notifications import render_lcd_lock_file

from .constants import (
    LCD_SUMMARY_MAX_WINDOW_MINUTES,
    LCD_SUMMARY_MIN_WINDOW_MINUTES,
    LCD_SUMMARY_WINDOW_LABEL,
    LCD_SUMMARY_WINDOW_MINUTES,
    LLM_SUMMARY_SUITE_FEATURE_SLUG,
)
from .models import LLMSummaryConfig

logger = logging.getLogger(__name__)

LCD_COLUMNS = 16
LCD_ROWS = 2
LCD_SUMMARY_BUFFER_CELLS = LCD_COLUMNS * LCD_ROWS
LCD_SUMMARY_FRAME_COUNT = 10
LCD_SUMMARY_EXPIRES_AFTER = timedelta(minutes=10)
DEFAULT_MODEL_DIR = Path(settings.BASE_DIR) / "work" / "llm" / "lcd-summary"
DEFAULT_MODEL_FILE = "MODEL.README"

UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?\s+")
LOG_TIMESTAMP_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:,(?P<fraction>\d+))?"
)
LEVEL_RE = re.compile(r"\b(INFO|DEBUG|WARNING|ERROR|CRITICAL)\b")
WHITESPACE_RE = re.compile(r"\s+")
ATTENTION_LOG_RE = re.compile(
    r"\b(?:WARNING|ERROR|CRITICAL|WRN|ERR|CRI)\b|raised unexpected",
    re.IGNORECASE,
)
HOST_RESOURCE_BODY_RE = re.compile(
    r"\bt\d+(?:\.\d+)?[cf]?\b.*\bd\d+%.*\bm\d+%",
    re.IGNORECASE,
)
HOST_ATTENTION_BODY_RE = re.compile(
    r"\b(?:action|alert|attention|blocked|check|critical|down|err(?:or)?|exception|fail(?:ed|ure)?|fix|offline|panic|warn(?:ing)?)\b",
    re.IGNORECASE,
)
INLINE_BUFFER_RE = re.compile(r"^[A-Z0-9][A-Z0-9 /&+.\-]{0,15}:.+")
SUMMARY_STATUS_COUNT_RE = re.compile(
    r"^(?P<count>\d+)\s*(?P<unit>lines?|lns?|x)\b(?:\s*/\s*(?P<label>\d+\s*[smhd]))?(?P<rest>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LogChunk:
    path: Path
    content: str


@dataclass(frozen=True)
class SummaryContextWindow:
    minutes: int
    label: str
    min_minutes: int
    max_minutes: int
    reasons: tuple[str, ...] = ()


def get_summary_config() -> LLMSummaryConfig:
    """Return the singleton LCD summary configuration record."""

    config, _created = LLMSummaryConfig.objects.get_or_create(
        slug="lcd-log-summary",
        defaults={"display": "LCD Log Summary"},
    )
    return config


def _resolve_model_path(config: LLMSummaryConfig) -> Path:
    suite_model_path = get_feature_parameter(
        LLM_SUMMARY_SUITE_FEATURE_SLUG,
        "model_path",
        fallback="",
    )
    if suite_model_path:
        return Path(suite_model_path)
    if config.model_path:
        return Path(config.model_path)
    env_override = os.getenv("ARTHEXIS_LLM_SUMMARY_MODEL")
    if env_override:
        return Path(env_override)
    return DEFAULT_MODEL_DIR


def resolve_model_path(config: LLMSummaryConfig) -> Path:
    """Return the effective local model directory for ``config``."""

    return _resolve_model_path(config)


def ensure_local_model(
    config: LLMSummaryConfig, *, preferred_path: str | Path | None = None
) -> Path:
    """Ensure the local summary artifact directory exists and record it on ``config``."""

    if preferred_path:
        model_dir = Path(preferred_path)
    else:
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


def _coerce_window_minutes(value: object, default: int) -> int:
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(minutes, 24 * 60))


def get_summary_context_window_bounds() -> tuple[int, int]:
    """Return configurable min/max summary context bounds in minutes."""

    min_minutes = _coerce_window_minutes(
        get_feature_parameter(
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
            "min_context_minutes",
            fallback=os.getenv(
                "ARTHEXIS_LLM_SUMMARY_MIN_CONTEXT_MINUTES",
                str(LCD_SUMMARY_MIN_WINDOW_MINUTES),
            ),
        ),
        LCD_SUMMARY_MIN_WINDOW_MINUTES,
    )
    max_minutes = _coerce_window_minutes(
        get_feature_parameter(
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
            "max_context_minutes",
            fallback=os.getenv(
                "ARTHEXIS_LLM_SUMMARY_MAX_CONTEXT_MINUTES",
                str(LCD_SUMMARY_MAX_WINDOW_MINUTES),
            ),
        ),
        LCD_SUMMARY_MAX_WINDOW_MINUTES,
    )
    return tuple(sorted((min_minutes, max_minutes)))


def _read_cpu_temperature_c() -> float | None:
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw_temp = temp_path.read_text(encoding="utf-8").strip()
        temp_c = float(raw_temp)
    except (OSError, ValueError):
        return None
    if temp_c > 1000:
        temp_c = temp_c / 1000
    return temp_c


def _read_load_pressure_ratio() -> float | None:
    try:
        load_1m, _load_5m, _load_15m = os.getloadavg()
    except (AttributeError, OSError):
        return None
    cpu_count = os.cpu_count() or 1
    return load_1m / max(cpu_count, 1)


def _read_memory_available_percent() -> float | None:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    values: dict[str, int] = {}
    for line in lines:
        key, _separator, rest = line.partition(":")
        if key not in {"MemTotal", "MemAvailable"}:
            continue
        try:
            values[key] = int(rest.strip().split()[0])
        except (IndexError, ValueError):
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return (available / total) * 100


def _scaled_window(min_minutes: int, max_minutes: int, divisor: int) -> int:
    return max(min_minutes, round(max_minutes / divisor))


def resolve_summary_context_window() -> SummaryContextWindow:
    """Return the adaptive LCD summary context window for the current host state."""

    min_minutes, max_minutes = get_summary_context_window_bounds()
    selected = max_minutes
    reasons: list[str] = []

    temp_c = _read_cpu_temperature_c()
    if temp_c is not None:
        if temp_c >= 80:
            selected = min(selected, min_minutes)
            reasons.append(f"temp={temp_c:.0f}C")
        elif temp_c >= 75:
            selected = min(selected, _scaled_window(min_minutes, max_minutes, 4))
            reasons.append(f"temp={temp_c:.0f}C")
        elif temp_c >= 70:
            selected = min(selected, _scaled_window(min_minutes, max_minutes, 2))
            reasons.append(f"temp={temp_c:.0f}C")

    load_ratio = _read_load_pressure_ratio()
    if load_ratio is not None:
        if load_ratio >= 4:
            selected = min(selected, min_minutes)
            reasons.append(f"load={load_ratio:.1f}x")
        elif load_ratio >= 2:
            selected = min(selected, _scaled_window(min_minutes, max_minutes, 4))
            reasons.append(f"load={load_ratio:.1f}x")

    mem_available = _read_memory_available_percent()
    if mem_available is not None:
        if mem_available <= 10:
            selected = min(selected, min_minutes)
            reasons.append(f"mem={mem_available:.0f}%")
        elif mem_available <= 20:
            selected = min(selected, _scaled_window(min_minutes, max_minutes, 4))
            reasons.append(f"mem={mem_available:.0f}%")

    return SummaryContextWindow(
        minutes=selected,
        label=f"{selected}m",
        min_minutes=min_minutes,
        max_minutes=max_minutes,
        reasons=tuple(reasons),
    )


def _parse_log_timestamp(line: str) -> datetime | None:
    match = LOG_TIMESTAMP_RE.match(line)
    if not match:
        return None

    try:
        parsed = datetime.strptime(
            match.group("stamp").replace("T", " "),
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None

    fraction = match.group("fraction")
    if fraction:
        parsed = parsed.replace(microsecond=int(fraction[:6].ljust(6, "0")))
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _is_attention_log_line(line: str) -> bool:
    return bool(ATTENTION_LOG_RE.search(line))


def _filter_log_content_since(
    content: str,
    since: datetime,
    *,
    attention_since: datetime | None = None,
) -> str:
    lines: list[str] = []
    saw_timestamp = False
    include_continuation = False

    for line in content.splitlines():
        timestamp = _parse_log_timestamp(line)
        if timestamp is None:
            if saw_timestamp and include_continuation:
                lines.append(line)
            continue

        saw_timestamp = True
        include_continuation = timestamp >= since or (
            attention_since is not None
            and timestamp >= attention_since
            and _is_attention_log_line(line)
        )
        if include_continuation:
            lines.append(line)

    if not saw_timestamp:
        return content
    return "\n".join(lines)


def _safe_offset(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def collect_recent_logs(
    config: LLMSummaryConfig,
    *,
    since: datetime,
    attention_since: datetime | None = None,
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
        since_ts = (attention_since or since).timestamp()
        if stat.st_mtime < since_ts:
            continue
        size = stat.st_size
        offset = _safe_offset(offsets.get(str(path)))
        if offset > size:
            offset = 0
        if size <= offset:
            offsets[str(path)] = size
            continue
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                content = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        content = _filter_log_content_since(
            content,
            since,
            attention_since=attention_since,
        )
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


def build_summary_prompt(
    compacted_logs: str,
    *,
    now: datetime,
    window: SummaryContextWindow | None = None,
) -> str:
    if window is None:
        window = SummaryContextWindow(
            minutes=LCD_SUMMARY_WINDOW_MINUTES,
            label=LCD_SUMMARY_WINDOW_LABEL,
            min_minutes=LCD_SUMMARY_MIN_WINDOW_MINUTES,
            max_minutes=LCD_SUMMARY_MAX_WINDOW_MINUTES,
        )
    cutoff = (now - timedelta(minutes=window.minutes)).strftime("%H:%M")
    pressure_note = (
        f" The active window was reduced because of: {', '.join(window.reasons)}."
        if window.reasons
        else ""
    )
    instructions = textwrap.dedent(f"""
        LCD_CONTEXT_WINDOW_LABEL: {window.label}
        You summarize system logs as 16x2 LCD buffers. Focus on the last {window.minutes} minutes (cutoff {cutoff}).
        Older warning, error, and critical lines from the last {window.max_minutes} minutes may be included so important logs stay visible when the active window shrinks.{pressure_note}
        Highlight urgent operator actions or failures. Think in 32 visible cells per screen, not as a document.
        Output 8-10 LCD screens. Each screen is two 16-cell rows.
        Row 1 is the log extract, status phrase, or longer description.
        Row 2 starts with a compact count such as "12 ln/{window.label}" for log lines or "3x/{window.label}" for repeated events.
        Never write "line" or "lines" on row 2; use "ln".
        Use the remaining right-side cells on row 2 for one operator word such as NORMAL, WARNING, ERROR, CHECK, FIX, or WAIT.
        Keep short phrases on one row when they fit; for example, "Journal failed 3" must not be split after "Journal".
        Shorten words aggressively, drop grammar when helpful, and use abbreviations, symbols, arrows, or LCD-friendly drawing characters when they compress meaning.
        Do not emit routine Host screens; RAM, disk, swap, CPU, and temperature already have dedicated LCD screens.
        Format:
        SCREEN 1:
        <log extract or description>
        <count>        <OPERATOR-WORD>
        ---
        SCREEN 2:
        <log extract or description>
        <count>        <OPERATOR-WORD>
        ...
        Only output the screens, no extra commentary.
        """).strip()
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
        if len(group) == 1:
            if INLINE_BUFFER_RE.match(group[0]):
                screens.append((group[0], ""))
            continue
        screens.append((group[0], " ".join(group[1:])))
    return screens


def filter_redundant_lcd_summary_screens(
    screens: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Drop summary frames already covered by dedicated LCD status screens."""

    filtered: list[tuple[str, str]] = []
    for subject, body in screens:
        subject_text = (subject or "").strip().lower()
        subject_header, _separator, subject_body = subject_text.partition(":")
        body_text = " ".join(
            part
            for part in (subject_body.strip(), (body or "").strip().lower())
            if part
        )
        if subject_header == "host" and not HOST_ATTENTION_BODY_RE.search(body_text):
            continue
        if subject_header in {"resource", "resources"} and HOST_RESOURCE_BODY_RE.search(
            body_text
        ):
            continue
        filtered.append((subject, body))
    return filtered


def _normalize_lcd_text(text: str, *, collapse_whitespace: bool = True) -> str:
    normalized = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    if collapse_whitespace:
        normalized = WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def normalize_summary_status_row(
    row: str,
    *,
    window_label: str = LCD_SUMMARY_WINDOW_LABEL,
) -> str:
    """Return a normalized LCD summary status row when it starts with a count."""

    raw = _normalize_lcd_text(row, collapse_whitespace=False)
    text = _normalize_lcd_text(row)
    match = SUMMARY_STATUS_COUNT_RE.match(text)
    if not match:
        return raw

    count = match.group("count")
    unit = match.group("unit").lower()
    effective_label = (
        WHITESPACE_RE.sub("", match.group("label"))
        if match.group("label")
        else window_label
    )
    metric = (
        f"{count}x/{effective_label}"
        if unit == "x"
        else f"{count} ln/{effective_label}"
    )
    evaluation = _normalize_lcd_text(match.group("rest")).upper()
    return _format_summary_status_row(metric, evaluation)


def _format_summary_status_row(metric: str, evaluation: str) -> str:
    left = _normalize_lcd_text(metric)
    right = _normalize_lcd_text(evaluation).upper()
    if not left:
        return right[:LCD_COLUMNS]
    if not right:
        return left[:LCD_COLUMNS]
    if len(left) + len(right) == LCD_COLUMNS:
        return f"{left}{right}"
    if len(left) + len(right) > LCD_COLUMNS:
        return f"{left} {right}"[:LCD_COLUMNS]
    return f"{left}{' ' * (LCD_COLUMNS - len(left) - len(right))}{right}"


def _normalize_summary_buffer(
    subject: str,
    body: str,
    *,
    window_label: str = LCD_SUMMARY_WINDOW_LABEL,
) -> tuple[str, str]:
    subject_text = _normalize_lcd_text(subject)
    body_text = _normalize_lcd_text(body, collapse_whitespace=False)
    body_text = normalize_summary_status_row(body_text, window_label=window_label)
    if body_text:
        return (
            subject_text[:LCD_COLUMNS].ljust(LCD_COLUMNS),
            body_text[:LCD_COLUMNS].ljust(LCD_COLUMNS),
        )

    combined = subject_text
    combined = combined[:LCD_SUMMARY_BUFFER_CELLS]
    line1 = combined[:LCD_COLUMNS].ljust(LCD_COLUMNS)
    line2 = combined[LCD_COLUMNS:LCD_SUMMARY_BUFFER_CELLS].ljust(LCD_COLUMNS)
    return line1, line2


def normalize_screens(
    screens: Iterable[tuple[str, str]],
    *,
    window_label: str = LCD_SUMMARY_WINDOW_LABEL,
) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for subject, body in screens:
        normalized.append(
            _normalize_summary_buffer(subject, body, window_label=window_label)
        )
    return normalized


def fixed_frame_window(screens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return a bounded LCD summary frame list without padding low-value blanks."""

    return list(screens[:LCD_SUMMARY_FRAME_COUNT])


def render_lcd_payload(subject: str, body: str, *, expires_at=None) -> str:
    return render_lcd_lock_file(subject=subject, body=body, expires_at=expires_at)


def execute_log_summary_generation(*, ignore_suite_feature_gate: bool = False) -> str:
    """Generate LCD log summary output and persist latest run metadata."""

    from apps.nodes.models import Node
    from apps.screens.startup_notifications import LCD_SUMMARY_LOCK_FILE
    from apps.tasks.tasks import (
        LocalLLMSummarizer,
        _write_lcd_frames,
    )

    lock_dir = Path(settings.BASE_DIR) / ".locks"

    node = Node.get_local()
    if not node:
        return "skipped:no-node"
    if not node_is_control(node):
        return "skipped:non-control-node"

    if not ignore_suite_feature_gate and not is_suite_feature_enabled(
        LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True
    ):
        logger.info(
            "Skipping LCD summary automation because suite feature '%s' is disabled.",
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
        )
        return "skipped:suite-feature-disabled"

    if not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"

    config = get_summary_config()
    if not config.is_active:
        return "skipped:inactive"

    ensure_local_model(config)

    now = timezone.now()
    window = resolve_summary_context_window()
    since = now - timedelta(minutes=window.minutes)
    attention_since = now - timedelta(minutes=window.max_minutes)
    chunks = collect_recent_logs(config, since=since, attention_since=attention_since)
    compacted_logs = compact_log_chunks(chunks)
    if not compacted_logs:
        config.last_run_at = now
        config.save(
            update_fields=[
                "last_run_at",
                "log_offsets",
                "model_path",
                "installed_at",
                "updated_at",
            ]
        )
        return "skipped:no-logs"

    prompt = build_summary_prompt(compacted_logs, now=now, window=window)
    summarizer = LocalLLMSummarizer()
    output = summarizer.summarize(prompt)
    screens = normalize_screens(
        filter_redundant_lcd_summary_screens(parse_screens(output)),
        window_label=window.label,
    )

    if not screens:
        screens = normalize_screens([("No events", "-"), ("Chk logs", "manual")])

    lock_file = lock_dir / LCD_SUMMARY_LOCK_FILE
    frames = fixed_frame_window(screens)
    if node.has_feature("lcd-screen"):
        _write_lcd_frames(
            frames,
            lock_file=lock_file,
            expires_at=now + LCD_SUMMARY_EXPIRES_AFTER,
        )
    elif lock_dir.exists():
        try:
            _write_lcd_frames([], lock_file=lock_file)
        except OSError:
            pass

    config.last_run_at = now
    config.last_prompt = prompt
    config.last_output = output
    config.save(
        update_fields=[
            "last_run_at",
            "last_prompt",
            "last_output",
            "log_offsets",
            "model_path",
            "installed_at",
            "updated_at",
        ]
    )
    return f"wrote:{len(frames)}"
