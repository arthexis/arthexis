from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import textwrap
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.features.parameters import get_feature_parameter
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.roles import node_is_control

from .constants import (
    LCD_SUMMARY_MAX_WINDOW_MINUTES,
    LCD_SUMMARY_MIN_WINDOW_MINUTES,
    LCD_SUMMARY_OPERATOR_WORDS,
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
SUMMARY_OUTPUT_DEFAULT_PATH = Path("logs") / "summary" / "latest.txt"
SUMMARY_OUTPUT_DIRECTORY = SUMMARY_OUTPUT_DEFAULT_PATH.parent
SUMMARY_SOURCE_GROUPS_DEFAULT = "logs,state,journal"
SUMMARY_SOURCE_MAX_BYTES_DEFAULT = 12_000
SUMMARY_SOURCE_MAX_BYTES_MIN = 2_048
SUMMARY_SOURCE_MAX_BYTES_LIMIT = 65_536
SUMMARY_SOURCE_COMMAND_TIMEOUT_SECONDS = 4
SUMMARY_STATE_SOURCE_MAX_BYTES = 4_096
SUMMARY_JOURNAL_SOURCE_MAX_BYTES = 8_192
SUMMARY_JOURNAL_LINES_PER_UNIT = 40
SUMMARY_SOURCE_GROUPS = frozenset({"logs", "state", "journal"})
SUMMARY_JOURNAL_UNITS = (
    "arthexis.service",
    "celery-arthexis.service",
    "celery-beat-arthexis.service",
    "lcd-arthexis.service",
    "rfid-arthexis.service",
    "arthexis-usb-inventory.service",
)

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


@dataclass(frozen=True)
class SummarySourceContext:
    config: LLMSummaryConfig
    since: datetime
    base_dir: Path
    log_dir: Path
    attention_since: datetime | None = None


@dataclass(frozen=True)
class SummarySource:
    name: str
    group: str
    priority: int
    max_bytes: int
    collector: Callable[[SummarySourceContext, SummarySource], list[LogChunk]]


SUMMARY_CONFIG_SLUG = "log-summary"
LEGACY_SUMMARY_CONFIG_SLUG = "lcd-log-summary"


def get_summary_config() -> LLMSummaryConfig:
    """Return the singleton file-summary configuration, reconciling legacy rows."""

    config = LLMSummaryConfig.objects.filter(slug=SUMMARY_CONFIG_SLUG).first()
    if config is None:
        config = LLMSummaryConfig.objects.filter(
            slug=LEGACY_SUMMARY_CONFIG_SLUG
        ).first()
        if config is not None:
            config.slug = SUMMARY_CONFIG_SLUG
            config.display = "Log Summary"
            config.save(update_fields=["slug", "display", "updated_at"])
        else:
            config = LLMSummaryConfig.objects.create(slug=SUMMARY_CONFIG_SLUG)
    return apply_summary_feature_parameters(config)


def apply_summary_feature_parameters(config: LLMSummaryConfig) -> LLMSummaryConfig:
    """Return the runtime summary config.

    Summary generation is deterministic-only; suite feature metadata is still
    used by the source collection and output helpers below.
    """

    return config


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


def _safe_positive_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _summary_source_groups() -> set[str]:
    raw_groups = get_feature_parameter(
        LLM_SUMMARY_SUITE_FEATURE_SLUG,
        "enabled_sources",
        fallback=SUMMARY_SOURCE_GROUPS_DEFAULT,
    )
    groups = {
        group.strip().lower()
        for group in (raw_groups or SUMMARY_SOURCE_GROUPS_DEFAULT).split(",")
        if group.strip()
    }
    if "all" in groups:
        return set(SUMMARY_SOURCE_GROUPS)
    if "systemd" in groups:
        groups.add("journal")
    known_groups = groups & SUMMARY_SOURCE_GROUPS
    return known_groups or set(SUMMARY_SOURCE_GROUPS_DEFAULT.split(","))


def _summary_source_byte_budget() -> int:
    raw_budget = get_feature_parameter(
        LLM_SUMMARY_SUITE_FEATURE_SLUG,
        "max_source_bytes",
        fallback=str(SUMMARY_SOURCE_MAX_BYTES_DEFAULT),
    )
    return _safe_positive_int(
        raw_budget,
        default=SUMMARY_SOURCE_MAX_BYTES_DEFAULT,
        minimum=SUMMARY_SOURCE_MAX_BYTES_MIN,
        maximum=SUMMARY_SOURCE_MAX_BYTES_LIMIT,
    )


def _limit_text_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    clipped = encoded[-max_bytes:].decode("utf-8", errors="replace")
    return f"...<truncated {len(encoded) - max_bytes} bytes>\n{clipped}"


def _read_tail_text(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            content = handle.read(max_bytes)
    except OSError:
        return ""
    text = content.decode("utf-8", errors="replace")
    if size > max_bytes:
        return f"...<truncated {size - max_bytes} bytes>\n{text}"
    return text


def _virtual_chunk(name: str, content: str) -> LogChunk | None:
    text = content.strip()
    if not text:
        return None
    return LogChunk(path=Path(name), content=text)


def _collect_log_file_source(
    context: SummarySourceContext, source: SummarySource
) -> list[LogChunk]:
    offsets = dict(context.config.log_offsets or {})
    chunks: list[LogChunk] = []
    remaining_bytes = source.max_bytes

    if not context.log_dir.exists():
        logger.warning("Log directory missing: %s", context.log_dir)
        context.config.log_offsets = offsets
        return chunks

    candidates = sorted(context.log_dir.rglob("*.log"))
    for path in candidates:
        path_key = str(path)
        try:
            stat = path.stat()
        except OSError:
            continue
        size = stat.st_size
        offset_known = path_key in offsets
        offset = _safe_offset(offsets.get(path_key))
        if offset > size:
            offset = 0
        since_ts = (context.attention_since or context.since).timestamp()
        if stat.st_mtime < since_ts and not (offset_known and size > offset):
            continue
        if size <= offset:
            offsets[path_key] = size
            continue
        if remaining_bytes <= 0:
            offsets.setdefault(path_key, offset)
            continue
        read_start = offset
        truncated_bytes = 0
        read_bytes = min(size - offset, remaining_bytes)
        if size - offset > read_bytes:
            read_start = size - read_bytes
            truncated_bytes = read_start - offset
        try:
            with open(path, "rb") as handle:
                handle.seek(read_start)
                raw_content = handle.read(read_bytes)
                content = raw_content.decode(
                    "utf-8",
                    errors="replace",
                )
        except OSError:
            continue
        remaining_bytes -= len(raw_content)
        if not offset_known:
            content = _filter_log_content_since(
                content,
                context.since,
                attention_since=context.attention_since,
            )
        if content:
            if truncated_bytes:
                content = f"...<truncated {truncated_bytes} bytes>\n{content}"
            chunks.append(
                LogChunk(
                    path=path,
                    content=content,
                )
            )
        offsets[path_key] = size

    context.config.log_offsets = offsets
    return chunks


def _summary_state_paths(base_dir: Path) -> list[Path]:
    lock_dir = base_dir / ".locks"
    paths: list[Path] = []
    paths.extend(
        [
            lock_dir / "lcd-channels.lck",
            lock_dir / "rfid-scan.json",
            Path("/run/arthexis-usb/devices.json"),
            Path("/etc/arthexis-usb/claims.json"),
            lock_dir / "startup_duration.lck",
            lock_dir / "upgrade_duration.lck",
            lock_dir / "upgrade_in_progress.lck",
        ]
    )
    history_dir = base_dir / "logs"
    history_files = sorted(
        history_dir.glob("lcd-history-*.txt"),
        key=_safe_mtime,
        reverse=True,
    )
    paths.extend(history_files[:3])
    return paths


def _state_chunk_name(path: Path, base_dir: Path) -> str:
    try:
        label = path.relative_to(base_dir)
    except ValueError:
        label = path
    label_text = label.as_posix().strip("/").replace("/", ":")
    return f"state:{label_text}"


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _collect_state_file_source(
    context: SummarySourceContext, source: SummarySource
) -> list[LogChunk]:
    chunks: list[LogChunk] = []
    seen: set[Path] = set()
    per_file_budget = min(source.max_bytes, SUMMARY_STATE_SOURCE_MAX_BYTES)
    remaining_bytes = per_file_budget
    for path in _summary_state_paths(context.base_dir):
        if remaining_bytes <= 0:
            break
        if path in seen or not path.exists() or not path.is_file():
            continue
        seen.add(path)
        content = _read_tail_text(path, remaining_bytes)
        if path.name == "rfid-scan.json":
            content = _sanitize_rfid_scan_state(content)
        remaining_bytes -= len(content.encode("utf-8", errors="replace"))
        chunk = _virtual_chunk(
            _state_chunk_name(path, context.base_dir),
            f"path={path}\n{content}",
        )
        if chunk:
            chunks.append(chunk)
    return chunks


def _sanitize_rfid_scan_state(content: str) -> str:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return "{}"
    if not isinstance(payload, dict):
        return "{}"
    payload.pop("deep_read", None)
    payload.pop("dump", None)
    payload.pop("keys", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _run_summary_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SUMMARY_SOURCE_COMMAND_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if completed.returncode != 0:
        return ""
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return output.strip()


def _collect_systemctl_failed_source(
    context: SummarySourceContext, source: SummarySource
) -> list[LogChunk]:
    output = _run_summary_command(["systemctl", "--failed", "--no-pager", "--plain"])
    if not output or "0 loaded units listed" in output:
        return []
    output = _limit_text_bytes(
        output,
        min(source.max_bytes, SUMMARY_JOURNAL_SOURCE_MAX_BYTES),
    )
    chunk = _virtual_chunk("journal:systemctl-failed", output)
    return [chunk] if chunk else []


def _collect_journal_warning_source(
    context: SummarySourceContext, source: SummarySource
) -> list[LogChunk]:
    chunks: list[LogChunk] = []
    since = context.since.isoformat(sep=" ", timespec="seconds")
    remaining_bytes = min(source.max_bytes, SUMMARY_JOURNAL_SOURCE_MAX_BYTES)
    for unit in SUMMARY_JOURNAL_UNITS:
        if remaining_bytes <= 0:
            break
        output = _run_summary_command(
            [
                "journalctl",
                "-u",
                unit,
                "--since",
                since,
                "--priority",
                "emerg..warning",
                "--lines",
                str(SUMMARY_JOURNAL_LINES_PER_UNIT),
                "--no-pager",
                "--output",
                "short-iso",
            ]
        )
        if not output or output.startswith("-- No entries --"):
            continue
        content = _limit_text_bytes(output, remaining_bytes)
        remaining_bytes -= len(content.encode("utf-8", errors="replace"))
        chunk = _virtual_chunk(
            f"journal:{unit}",
            content,
        )
        if chunk:
            chunks.append(chunk)
    return chunks


def get_summary_sources(
    enabled_groups: set[str] | None = None,
) -> list[SummarySource]:
    """Return enabled, ordered sources for LCD log summaries."""

    groups = enabled_groups if enabled_groups is not None else _summary_source_groups()
    max_bytes = _summary_source_byte_budget()
    sources = [
        SummarySource(
            name="suite-log-files",
            group="logs",
            priority=10,
            max_bytes=max_bytes,
            collector=_collect_log_file_source,
        ),
        SummarySource(
            name="suite-state-files",
            group="state",
            priority=20,
            max_bytes=min(max_bytes, SUMMARY_STATE_SOURCE_MAX_BYTES),
            collector=_collect_state_file_source,
        ),
        SummarySource(
            name="systemctl-failed",
            group="journal",
            priority=30,
            max_bytes=min(max_bytes, SUMMARY_JOURNAL_SOURCE_MAX_BYTES),
            collector=_collect_systemctl_failed_source,
        ),
        SummarySource(
            name="suite-journal-warnings",
            group="journal",
            priority=40,
            max_bytes=min(max_bytes, SUMMARY_JOURNAL_SOURCE_MAX_BYTES),
            collector=_collect_journal_warning_source,
        ),
    ]
    return [source for source in sources if source.group in groups]


def collect_recent_logs(
    config: LLMSummaryConfig,
    *,
    since: datetime,
    attention_since: datetime | None = None,
    log_dir: Path | None = None,
    sources: Iterable[SummarySource] | None = None,
) -> list[LogChunk]:
    if log_dir is None:
        log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    context = SummarySourceContext(
        config=config,
        since=since,
        attention_since=attention_since,
        base_dir=Path(settings.BASE_DIR),
        log_dir=log_dir,
    )
    chunks: list[LogChunk] = []
    source_list = (
        sorted(sources, key=lambda source: source.priority)
        if sources is not None
        else get_summary_sources()
    )
    for source in source_list:
        try:
            chunks.extend(source.collector(context, source))
        except OSError:
            logger.warning("Summary source failed: %s", source.name, exc_info=True)
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
        Use the remaining right-side cells on row 2 for one compact operator word such as OK, WARN, ERROR, CHECK, FIX, or WAIT.
        Keep a visible space between the count and operator word; shorten the operator word before removing the gap.
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
    raw_right = _normalize_lcd_text(evaluation).upper()
    right = LCD_SUMMARY_OPERATOR_WORDS.get(raw_right, raw_right)
    if not left:
        return right[:LCD_COLUMNS]
    if not right:
        return left[:LCD_COLUMNS]
    if len(left) + 1 + len(right) > LCD_COLUMNS:
        available_left = LCD_COLUMNS - len(right) - 1
        if available_left <= 0:
            return right[:LCD_COLUMNS]
        return f"{left[:available_left].rstrip()} {right}"[:LCD_COLUMNS]
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


def summary_output_target(config: LLMSummaryConfig) -> str:
    """Return the configured output target, migrating legacy values to files."""

    target = str(
        getattr(config, "output_target", LLMSummaryConfig.OutputTarget.FILE) or ""
    ).strip()
    if target == LLMSummaryConfig.OutputTarget.FILE:
        return LLMSummaryConfig.OutputTarget.FILE
    return LLMSummaryConfig.OutputTarget.FILE


def resolve_summary_output_file_path(
    config: LLMSummaryConfig,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Return a summary file path confined to the summary output directory."""

    root = _summary_output_directory(base_dir=base_dir)
    raw_path = str(getattr(config, "output_file_path", "") or "").strip()
    configured_path = Path(raw_path) if raw_path else SUMMARY_OUTPUT_DEFAULT_PATH
    if configured_path.is_absolute():
        raise ValueError("Summary output file path must be relative.")
    if ".." in configured_path.parts:
        raise ValueError("Summary output file path cannot contain '..'.")
    if (
        configured_path.parts[: len(SUMMARY_OUTPUT_DIRECTORY.parts)]
        == SUMMARY_OUTPUT_DIRECTORY.parts
    ):
        path = Path(base_dir or settings.BASE_DIR) / configured_path
    else:
        path = root / configured_path
    _validate_summary_output_path(path, root=root)
    return path


def _summary_output_directory(*, base_dir: Path | None = None) -> Path:
    return Path(base_dir or settings.BASE_DIR) / SUMMARY_OUTPUT_DIRECTORY


def _validate_summary_output_path(path: Path, *, root: Path) -> None:
    root_resolved = root.resolve(strict=False)
    parent_resolved = path.parent.resolve(strict=False)
    if not parent_resolved.is_relative_to(root_resolved):
        raise ValueError(
            "Summary output file path escapes the summary output directory."
        )
    if path.exists():
        if not path.resolve(strict=True).is_relative_to(root_resolved):
            raise ValueError(
                "Summary output file path escapes the summary output directory."
            )


def summary_output_file_paths(
    config: LLMSummaryConfig,
    *,
    base_dir: Path | None = None,
) -> list[tuple[str, Path]]:
    """Return the concrete file paths to write for the configured format."""

    output_format = str(
        getattr(config, "output_file_format", LLMSummaryConfig.OutputFileFormat.TEXT)
        or ""
    ).strip()
    path = resolve_summary_output_file_path(config, base_dir=base_dir)
    if output_format == LLMSummaryConfig.OutputFileFormat.JSON:
        return [(LLMSummaryConfig.OutputFileFormat.JSON, path)]
    if output_format == LLMSummaryConfig.OutputFileFormat.BOTH:
        if path.suffix.lower() == ".json":
            return [
                (LLMSummaryConfig.OutputFileFormat.TEXT, path.with_suffix(".txt")),
                (LLMSummaryConfig.OutputFileFormat.JSON, path),
            ]
        return [
            (LLMSummaryConfig.OutputFileFormat.TEXT, path),
            (LLMSummaryConfig.OutputFileFormat.JSON, path.with_suffix(".json")),
        ]
    return [(LLMSummaryConfig.OutputFileFormat.TEXT, path)]


def _summary_frames_payload(frames: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "subject": str(subject or "").strip(),
            "body": str(body or "").strip(),
        }
        for subject, body in frames
    ]


def _render_summary_text_file(
    *,
    generated_at: datetime,
    window: SummaryContextWindow,
    output: str,
    frames: list[tuple[str, str]],
) -> str:
    frame_lines = [
        f"{index:02d}. {subject.strip()} | {body.strip()}"
        for index, (subject, body) in enumerate(frames, start=1)
    ]
    return "\n".join(
        [
            "Deterministic Summary",
            f"Generated: {generated_at.isoformat()}",
            f"Window: {window.label}",
            "",
            "Frames:",
            *(frame_lines or ["(none)"]),
            "",
            "Raw output:",
            str(output or "").strip(),
            "",
        ]
    )


def _render_summary_json_file(
    *,
    generated_at: datetime,
    window: SummaryContextWindow,
    output: str,
    frames: list[tuple[str, str]],
) -> str:
    payload = {
        "generated_at": generated_at.isoformat(),
        "window": {
            "minutes": window.minutes,
            "label": window.label,
            "min_minutes": window.min_minutes,
            "max_minutes": window.max_minutes,
            "reasons": list(window.reasons),
        },
        "frames": _summary_frames_payload(frames),
        "output": output,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_texts([(path, content)])


def _atomic_write_texts(writes: Iterable[tuple[Path, str]]) -> None:
    staged_writes: list[tuple[Path, Path]] = []
    try:
        for path, content in writes:
            staged_writes.append((path, _write_text_temp(path, content)))
        _promote_staged_writes(staged_writes)
    finally:
        for _, temp_path in staged_writes:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _write_text_temp(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"

    def opener(
        file: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int
    ) -> int:
        return os.open(file, flags, mode=0o600)

    try:
        with open(temp_path, "x", encoding="utf-8", opener=opener) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return temp_path


def _promote_staged_writes(staged_writes: list[tuple[Path, Path]]) -> None:
    backups: list[tuple[Path, Path]] = []
    promoted_paths: list[Path] = []
    try:
        for path, _ in staged_writes:
            if not path.exists():
                continue
            backup_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.bak"
            os.replace(path, backup_path)
            backups.append((path, backup_path))

        for path, temp_path in staged_writes:
            os.replace(temp_path, path)
            promoted_paths.append(path)
            _fsync_parent_directory(path)
    except Exception:
        for path in promoted_paths:
            try:
                path.unlink()
            except OSError:
                pass
        for path, backup_path in reversed(backups):
            if not backup_path.exists():
                continue
            try:
                os.replace(backup_path, path)
                _fsync_parent_directory(path)
            except OSError:
                pass
        raise
    finally:
        for _, backup_path in backups:
            try:
                backup_path.unlink()
            except OSError:
                pass


def _fsync_parent_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        dir_fd = os.open(path.parent, flags)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_summary_output_files(
    config: LLMSummaryConfig,
    *,
    generated_at: datetime,
    window: SummaryContextWindow,
    output: str,
    frames: list[tuple[str, str]],
    base_dir: Path | None = None,
) -> list[Path]:
    """Write durable summary artifacts and return the paths written."""

    writes: list[tuple[Path, str]] = []
    for output_format, path in summary_output_file_paths(config, base_dir=base_dir):
        if output_format == LLMSummaryConfig.OutputFileFormat.JSON:
            content = _render_summary_json_file(
                generated_at=generated_at,
                window=window,
                output=output,
                frames=frames,
            )
        else:
            content = _render_summary_text_file(
                generated_at=generated_at,
                window=window,
                output=output,
                frames=frames,
            )
        writes.append((path, content))
    _atomic_write_texts(writes)
    return [path for path, _ in writes]


def _record_summary_error(
    config: LLMSummaryConfig,
    *,
    now: datetime,
    prompt: str,
    previous_log_offsets: dict[str, object],
) -> None:
    config.last_run_at = now
    config.last_prompt = prompt
    config.last_output = ""
    config.log_offsets = previous_log_offsets
    update_fields = [
        "last_run_at",
        "last_prompt",
        "last_output",
        "log_offsets",
        "updated_at",
    ]
    config.save(update_fields=update_fields)


def execute_log_summary_generation(*, ignore_suite_feature_gate: bool = False) -> str:
    """Generate log summary output and persist latest run metadata."""

    from apps.nodes.models import Node
    from apps.summary.tasks import (
        LocalLLMSummarizer,
        LocalLLMSummaryError,
    )

    node = Node.get_local()
    if not node:
        return "skipped:no-node"
    if not node_is_control(node):
        return "skipped:non-control-node"

    if not ignore_suite_feature_gate and not is_suite_feature_enabled(
        LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True
    ):
        logger.info(
            "Skipping summary automation because suite feature '%s' is disabled.",
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
        )
        return "skipped:suite-feature-disabled"

    if not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"

    config = get_summary_config()
    if not config.is_active:
        return "skipped:inactive"

    now = timezone.now()
    window = resolve_summary_context_window()
    since = now - timedelta(minutes=window.minutes)
    attention_since = now - timedelta(minutes=window.max_minutes)
    previous_log_offsets = dict(getattr(config, "log_offsets", {}) or {})
    chunks = collect_recent_logs(config, since=since, attention_since=attention_since)
    compacted_logs = compact_log_chunks(chunks)
    if not compacted_logs:
        config.last_run_at = now
        config.save(
            update_fields=[
                "last_run_at",
                "log_offsets",
                "updated_at",
            ]
        )
        return "skipped:no-logs"

    prompt = build_summary_prompt(compacted_logs, now=now, window=window)
    summarizer = LocalLLMSummarizer(config=config)
    try:
        output = summarizer.summarize(prompt)
    except LocalLLMSummaryError:
        logger.exception("Failed to generate deterministic summary")
        _record_summary_error(
            config,
            now=now,
            prompt=prompt,
            previous_log_offsets=previous_log_offsets,
        )
        return "error:summary-generation"
    parsed_screens = parse_screens(output)
    screens = normalize_screens(
        filter_redundant_lcd_summary_screens(parsed_screens),
        window_label=window.label,
    )

    if not screens:
        screens = normalize_screens([("No events", "-"), ("Chk logs", "manual")])

    output_target = summary_output_target(config)
    if output_target != LLMSummaryConfig.OutputTarget.FILE:
        output_target = LLMSummaryConfig.OutputTarget.FILE
        config.output_target = output_target
    frames = fixed_frame_window(screens)
    written_files: list[Path] = []
    file_output_failed = False
    if output_target == LLMSummaryConfig.OutputTarget.FILE:
        try:
            written_files = write_summary_output_files(
                config,
                generated_at=now,
                window=window,
                output=output,
                frames=frames,
                base_dir=Path(settings.BASE_DIR),
            )
        except (OSError, TypeError, ValueError):
            logger.exception("Failed to write summary file output")
            file_output_failed = True

    config.last_run_at = now
    config.last_prompt = prompt
    config.last_output = output
    update_fields = [
        "last_run_at",
        "last_prompt",
        "last_output",
        "log_offsets",
        "updated_at",
    ]
    if config.output_target == LLMSummaryConfig.OutputTarget.FILE:
        update_fields.append("output_target")
    if written_files:
        config.last_output_file_path = str(written_files[0])
        update_fields.append("last_output_file_path")
    elif output_target == LLMSummaryConfig.OutputTarget.FILE and getattr(
        config, "last_output_file_path", ""
    ):
        config.last_output_file_path = ""
        update_fields.append("last_output_file_path")
    config.save(update_fields=update_fields)
    if file_output_failed:
        return "error:file-output"
    if output_target == LLMSummaryConfig.OutputTarget.FILE:
        return f"wrote-file:{len(written_files)}"
    return f"wrote:{len(frames)}"
