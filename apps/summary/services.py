from __future__ import annotations

import logging
import os
import re
import subprocess
import textwrap
from collections.abc import Callable, Iterable
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
SUMMARY_SOURCE_GROUPS_DEFAULT = "logs,state,journal"
SUMMARY_SOURCE_MAX_BYTES_DEFAULT = 12_000
SUMMARY_SOURCE_MAX_BYTES_MIN = 2_048
SUMMARY_SOURCE_MAX_BYTES_LIMIT = 65_536
SUMMARY_SOURCE_COMMAND_TIMEOUT_SECONDS = 4
SUMMARY_STATE_SOURCE_MAX_BYTES = 4_096
SUMMARY_JOURNAL_SOURCE_MAX_BYTES = 8_192
SUMMARY_JOURNAL_LINES_PER_UNIT = 40
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
LEVEL_RE = re.compile(r"\b(INFO|DEBUG|WARNING|ERROR|CRITICAL)\b")
WHITESPACE_RE = re.compile(r"\s+")
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
    r"^(?P<count>\d+)\s*(?P<unit>lines?|lns?|x)\b(?:\s*/\s*\d+\s*[smhd])?(?P<rest>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LogChunk:
    path: Path
    content: str


@dataclass(frozen=True)
class SummarySourceContext:
    config: LLMSummaryConfig
    since: datetime
    base_dir: Path
    log_dir: Path


@dataclass(frozen=True)
class SummarySource:
    name: str
    group: str
    priority: int
    max_bytes: int
    collector: Callable[[SummarySourceContext, SummarySource], list[LogChunk]]


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
        return {"logs", "state", "journal"}
    if "systemd" in groups:
        groups.add("journal")
    return groups or {"logs"}


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

    if not context.log_dir.exists():
        logger.warning("Log directory missing: %s", context.log_dir)
        context.config.log_offsets = offsets
        return chunks

    candidates = sorted(context.log_dir.rglob("*.log"))
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        since_ts = context.since.timestamp()
        if stat.st_mtime < since_ts:
            continue
        offset = _safe_offset(offsets.get(str(path)))
        size = stat.st_size
        if offset > size:
            offset = 0
        if size <= offset:
            offsets[str(path)] = size
            continue
        read_start = offset
        truncated_bytes = 0
        if size - offset > source.max_bytes:
            read_start = size - source.max_bytes
            truncated_bytes = read_start - offset
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(read_start)
                content = handle.read(source.max_bytes)
        except OSError:
            continue
        if content:
            if truncated_bytes:
                content = f"...<truncated {truncated_bytes} bytes>\n{content}"
            chunks.append(
                LogChunk(
                    path=path,
                    content=content,
                )
            )
        offsets[str(path)] = size

    context.config.log_offsets = offsets
    return chunks


def _summary_state_paths(base_dir: Path) -> list[Path]:
    lock_dir = base_dir / ".locks"
    paths: list[Path] = []
    paths.extend(sorted(lock_dir.glob("lcd-summary*")))
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
    for path in _summary_state_paths(context.base_dir):
        if path in seen or not path.exists() or not path.is_file():
            continue
        seen.add(path)
        content = _read_tail_text(path, per_file_budget)
        chunk = _virtual_chunk(
            _state_chunk_name(path, context.base_dir),
            f"path={path}\n{content}",
        )
        if chunk:
            chunks.append(chunk)
    return chunks


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
    since = context.since.strftime("%Y-%m-%d %H:%M:%S")
    per_unit_budget = min(source.max_bytes, SUMMARY_JOURNAL_SOURCE_MAX_BYTES)
    for unit in SUMMARY_JOURNAL_UNITS:
        output = _run_summary_command(
            [
                "journalctl",
                "-u",
                unit,
                "--since",
                since,
                "--priority",
                "warning..alert",
                "--lines",
                str(SUMMARY_JOURNAL_LINES_PER_UNIT),
                "--no-pager",
                "--output",
                "short-iso",
            ]
        )
        if not output or output.startswith("-- No entries --"):
            continue
        chunk = _virtual_chunk(
            f"journal:{unit}",
            _limit_text_bytes(output, per_unit_budget),
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
    log_dir: Path | None = None,
    sources: Iterable[SummarySource] | None = None,
) -> list[LogChunk]:
    if log_dir is None:
        log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    context = SummarySourceContext(
        config=config,
        since=since,
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


def build_summary_prompt(compacted_logs: str, *, now: datetime) -> str:
    cutoff = (now - timedelta(minutes=LCD_SUMMARY_WINDOW_MINUTES)).strftime("%H:%M")
    instructions = textwrap.dedent(f"""
        You summarize system logs as 16x2 LCD buffers. Focus on the last {LCD_SUMMARY_WINDOW_MINUTES} minutes (cutoff {cutoff}).
        Highlight urgent operator actions or failures. Think in 32 visible cells per screen, not as a document.
        Output 8-10 LCD screens. Each screen is two 16-cell rows.
        Row 1 is the log extract, status phrase, or longer description.
        Row 2 starts with a compact count such as "12 ln/{LCD_SUMMARY_WINDOW_LABEL}" for log lines or "3x/{LCD_SUMMARY_WINDOW_LABEL}" for repeated events.
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


def normalize_summary_status_row(row: str) -> str:
    """Return a normalized LCD summary status row when it starts with a count."""

    raw = _normalize_lcd_text(row, collapse_whitespace=False)
    text = _normalize_lcd_text(row)
    match = SUMMARY_STATUS_COUNT_RE.match(text)
    if not match:
        return raw

    count = match.group("count")
    unit = match.group("unit").lower()
    metric = (
        f"{count}x/{LCD_SUMMARY_WINDOW_LABEL}"
        if unit == "x"
        else f"{count} ln/{LCD_SUMMARY_WINDOW_LABEL}"
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


def _normalize_summary_buffer(subject: str, body: str) -> tuple[str, str]:
    subject_text = _normalize_lcd_text(subject)
    body_text = _normalize_lcd_text(body, collapse_whitespace=False)
    body_text = normalize_summary_status_row(body_text)
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


def normalize_screens(screens: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for subject, body in screens:
        normalized.append(_normalize_summary_buffer(subject, body))
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
    since = config.last_run_at or (now - timedelta(minutes=5))
    chunks = collect_recent_logs(config, since=since)
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

    prompt = build_summary_prompt(compacted_logs, now=now)
    summarizer = LocalLLMSummarizer()
    output = summarizer.summarize(prompt)
    screens = normalize_screens(
        filter_redundant_lcd_summary_screens(parse_screens(output))
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
