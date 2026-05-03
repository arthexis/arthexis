from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.features.parameters import get_feature_parameter
from apps.features.utils import is_suite_feature_enabled
from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE, render_lcd_lock_file

from .constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from .models import LLMSummaryConfig

logger = logging.getLogger(__name__)

LCD_COLUMNS = 16
LCD_SUMMARY_FRAME_COUNT = 10
LCD_SUMMARY_EXPIRES_AFTER = timedelta(minutes=10)
STATUS_COMMAND_TIMEOUT_SECONDS = 1.5
STATUS_JOURNAL_LOOKBACK = "2 hours ago"
STATUS_JOURNAL_MAX_LINES = 80
STATUS_SOURCE_LINE_LIMIT = 8
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
JOURNAL_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T(?P<hhmm>\d{2}:\d{2}):")

STATUS_JOURNAL_KEEP_PATTERNS = (
    "fat-fs",
    "fat read failed",
    "asking for cache data failed",
    "i/o error",
    "usb write fail",
    "failed to start",
    "failed with result",
)
STATUS_JOURNAL_DROP_PATTERNS = (
    "bluetoothd",
    "connection closed by remote host",
    "kex_exchange_identification",
    "src/plugin.c:init_plugin",
)
USB_ROLE_LABELS = {
    "bastion-unlock": "bastion",
    "kindle-postbox": "kindle",
}


@dataclass(frozen=True)
class LogChunk:
    path: Path
    content: str


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


def collect_noteworthy_status_lines() -> list[str]:
    """Return compact host status lines that are useful when log deltas are quiet."""

    lines: list[str] = []
    lines.extend(_systemd_failed_status_lines())
    lines.extend(_journal_status_lines())
    lines.extend(_usb_inventory_status_lines())

    host_line = _host_resource_status_line()
    if host_line:
        lines.append(host_line)

    return _dedupe_status_lines(lines)[:STATUS_SOURCE_LINE_LIMIT]


def _run_status_command(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=STATUS_COMMAND_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def _systemd_failed_status_lines() -> list[str]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return []

    result = _run_status_command(
        [systemctl, "--failed", "--no-legend", "--plain", "--no-pager"]
    )
    if result is None:
        return []
    if result.returncode not in {0, 1}:
        return []

    units: list[str] = []
    for raw_line in result.stdout.splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        units.append(parts[0].removesuffix(".service"))

    if not units:
        return ["OK status: 0 failed units"]

    shown = ", ".join(units[:3])
    suffix = "" if len(units) <= 3 else f" +{len(units) - 3}"
    return [f"ERR status: failed units {shown}{suffix}"]


def _journal_status_lines() -> list[str]:
    journalctl = shutil.which("journalctl")
    if not journalctl:
        return []

    result = _run_status_command(
        [
            journalctl,
            "-p",
            "3",
            "-b",
            "--since",
            STATUS_JOURNAL_LOOKBACK,
            "--lines",
            str(STATUS_JOURNAL_MAX_LINES),
            "--no-pager",
            "--output=short-iso",
        ]
    )
    if result is None or result.returncode not in {0, 1}:
        return []

    grouped: dict[str, dict[str, object]] = {}
    for raw_line in result.stdout.splitlines():
        if not _is_noteworthy_journal_line(raw_line):
            continue
        label = _journal_status_label(raw_line)
        entry = grouped.setdefault(label, {"count": 0, "last": ""})
        entry["count"] = int(entry["count"]) + 1
        line_time = _journal_line_time(raw_line)
        if line_time:
            entry["last"] = line_time

    lines: list[str] = []
    for label, entry in sorted(
        grouped.items(),
        key=lambda item: (-int(item[1]["count"]), item[0]),
    )[:3]:
        last = f" last {entry['last']}" if entry.get("last") else ""
        lines.append(f"ERR journal: {label} x{entry['count']}{last}")
    return lines


def _is_noteworthy_journal_line(raw_line: str) -> bool:
    lowered = raw_line.lower()
    if not lowered.strip():
        return False
    if any(pattern in lowered for pattern in STATUS_JOURNAL_DROP_PATTERNS):
        return False
    return any(pattern in lowered for pattern in STATUS_JOURNAL_KEEP_PATTERNS)


def _journal_status_label(raw_line: str) -> str:
    lowered = raw_line.lower()
    if "fat-fs" in lowered or "fat read failed" in lowered:
        return "USB FAT sda1"
    if "asking for cache data failed" in lowered:
        return "USB cache sda"
    if "usb write fail" in lowered:
        return "USB write fail"

    failed_start = re.search(r"Failed to start ([^:.]+)", raw_line)
    if failed_start:
        return f"failed start {failed_start.group(1).strip()[:24]}"
    if "failed with result" in lowered:
        return "unit failed"
    return "system error"


def _journal_line_time(raw_line: str) -> str:
    match = JOURNAL_TIME_RE.search(raw_line)
    if not match:
        return ""
    return match.group("hhmm")


def _usb_inventory_status_lines() -> list[str]:
    inventory = _read_usb_inventory()
    if not isinstance(inventory, dict):
        return []

    devices = inventory.get("devices")
    if not isinstance(devices, list):
        return []

    lines: list[str] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        if device.get("transport") != "usb" or device.get("type") != "part":
            continue

        name = str(device.get("name") or device.get("path") or "usb")
        label = str(device.get("label") or device.get("fstype") or "device")
        roles = device.get("claimed_roles") or []
        role = _usb_role_label(str(roles[0])) if roles else "mounted"
        mounts = device.get("mounts") or []
        if not mounts:
            lines.append(f"WRN usb: {name} {label} unmounted")
            continue

        is_read_only = any(
            isinstance(mount, dict) and mount.get("read_only") for mount in mounts
        )
        mode = "ro" if is_read_only else "rw"
        if role != "mounted":
            lines.append(f"OK usb: {name} {mode} {role}")
        else:
            lines.append(f"OK usb: {name} {label} {mode}")

    return lines[:2]


def _usb_role_label(role: str) -> str:
    return USB_ROLE_LABELS.get(role, role)


def _read_usb_inventory() -> dict[str, object] | None:
    path = Path(os.getenv("ARTHEXIS_USB_INVENTORY", "/run/arthexis-usb/devices.json"))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _host_resource_status_line() -> str:
    parts: list[str] = []

    temp_c = _read_cpu_temp_c()
    if temp_c is not None:
        parts.append(f"t{temp_c}C")

    try:
        usage = shutil.disk_usage(settings.BASE_DIR)
    except OSError:
        usage = None
    if usage and usage.total:
        disk_percent = round((usage.used / usage.total) * 100)
        parts.append(f"d{disk_percent}%")

    mem_percent = _read_memory_used_percent()
    if mem_percent is not None:
        parts.append(f"m{mem_percent}%")

    if not parts:
        return ""
    return f"OK host: {' '.join(parts)}"


def _read_cpu_temp_c() -> int | None:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw = path.read_text(encoding="utf-8").strip()
        value = float(raw)
    except (OSError, ValueError):
        return None
    if value > 1000:
        value = value / 1000
    return round(value)


def _read_memory_used_percent() -> int | None:
    path = Path("/proc/meminfo")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    values: dict[str, int] = {}
    for line in lines:
        key, _separator, rest = line.partition(":")
        if key not in {"MemTotal", "MemAvailable"}:
            continue
        raw_value = rest.strip().split()[0]
        try:
            values[key] = int(raw_value)
        except (IndexError, ValueError):
            continue

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    used = max(total - available, 0)
    return round((used / total) * 100)


def _dedupe_status_lines(lines: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        cleaned = WHITESPACE_RE.sub(" ", line).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def build_summary_prompt(compacted_logs: str, *, now: datetime) -> str:
    cutoff = (now - timedelta(minutes=4)).strftime("%H:%M")
    instructions = textwrap.dedent(
        f"""
        You summarize system logs for a 16x2 LCD. Focus on the last 4 minutes (cutoff {cutoff}).
        When LOGS contains current host-status lines, show the useful facts directly.
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


def fixed_frame_window(screens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return a bounded LCD summary frame list without padding low-value blanks."""

    return list(screens[:LCD_SUMMARY_FRAME_COUNT])


def render_lcd_payload(subject: str, body: str, *, expires_at=None) -> str:
    return render_lcd_lock_file(subject=subject, body=body, expires_at=expires_at)


def clear_legacy_low_summary_frames(lock_dir: Path) -> None:
    """Remove old summary frames written into the low LCD channel."""

    prefix = f"{LCD_LOW_LOCK_FILE}-"
    legacy_frames = [
        candidate
        for candidate in lock_dir.glob(f"{prefix}*")
        if candidate.name[len(prefix) :].isdigit()
    ]
    if not legacy_frames:
        return

    for candidate in [lock_dir / LCD_LOW_LOCK_FILE, *legacy_frames]:
        candidate.unlink(missing_ok=True)


def execute_log_summary_generation(*, ignore_suite_feature_gate: bool = False) -> str:
    """Generate LCD log summary output and persist latest run metadata."""

    from apps.nodes.models import Node
    from apps.screens.startup_notifications import LCD_SUMMARY_LOCK_FILE
    from apps.tasks.tasks import (
        LocalLLMSummarizer,
        _write_lcd_frames,
    )

    lock_dir = Path(settings.BASE_DIR) / ".locks"
    clear_legacy_low_summary_frames(lock_dir)

    node = Node.get_local()
    if not node:
        return "skipped:no-node"

    if (
        not ignore_suite_feature_gate
        and not is_suite_feature_enabled(LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True)
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
        status_lines = collect_noteworthy_status_lines()
        if status_lines:
            compacted_logs = "[status]\n" + "\n".join(status_lines)
        else:
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
    screens = normalize_screens(parse_screens(output))

    if not screens:
        screens = normalize_screens([("No events", "-"), ("Chk logs", "manual")])

    lock_file = lock_dir / LCD_SUMMARY_LOCK_FILE
    frames = fixed_frame_window(screens)
    _write_lcd_frames(
        frames,
        lock_file=lock_file,
        expires_at=now + LCD_SUMMARY_EXPIRES_AFTER,
    )

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
