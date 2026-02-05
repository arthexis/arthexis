from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
import logging
import os
import re
import socket
import subprocess
import shutil
from typing import Callable, Iterable

from django.conf import settings
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.urls import NoReverseMatch, reverse

from apps.core.systemctl import _systemctl_command
from apps.nginx.renderers import generate_primary_config
from apps.screens.startup_notifications import lcd_feature_enabled
from apps.cards.rfid_service import rfid_service_enabled
from utils import revision

from .filesystem import (
    _configured_backend_port,
    _nginx_site_path,
    _pid_file_running,
    _read_service_mode,
    _resolve_nginx_mode,
    _startup_report_log_path,
    _startup_report_reference_time,
    _suite_uptime_lock_info,
)


STARTUP_REPORT_DEFAULT_LIMIT = 50
STARTUP_CLOCK_DRIFT_THRESHOLD = timedelta(minutes=5)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemField:
    """Metadata describing a single entry on the system admin page."""

    label: str
    sigil_key: str
    value: object
    field_type: str = "text"

    @property
    def sigil(self) -> str:
        return f"SYS.{self.sigil_key}"


_RUNSERVER_PORT_PATTERN = re.compile(r":(\d{2,5})(?:\D|$)")
_RUNSERVER_PORT_FLAG_PATTERN = re.compile(r"--port(?:=|\s+)(\d{2,5})", re.IGNORECASE)


_DAY_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


SERVICE_REPORT_DEFINITIONS = (
    {
        "key": "suite",
        "label": _("Suite service"),
        "unit_template": "{service}.service",
        "pid_file": "django.pid",
        "docs": "services/suite-service.md",
    },
    {
        "key": "celery-worker",
        "label": _("Celery worker"),
        "unit_template": "celery-{service}.service",
        "pid_file": "celery_worker.pid",
        "docs": "services/celery-worker.md",
    },
    {
        "key": "celery-beat",
        "label": _("Celery beat"),
        "unit_template": "celery-beat-{service}.service",
        "pid_file": "celery_beat.pid",
        "docs": "services/celery-beat.md",
    },
    {
        "key": "lcd-screen",
        "label": _("LCD screen"),
        "unit_template": "lcd-{service}.service",
        "pid_file": "lcd.pid",
        "docs": "services/lcd-screen.md",
    },
    {
        "key": "rfid-service",
        "label": _("RFID scanner service"),
        "unit_template": "rfid-{service}.service",
        "docs": "services/rfid-scanner-service.md",
    },
)



def _format_timestamp(dt: datetime | None) -> str:
    """Return ``dt`` formatted using the active ``DATETIME_FORMAT``."""

    if dt is None:
        return ""
    try:
        localized = timezone.localtime(dt)
    except Exception:
        localized = dt
    return date_format(localized, "DATETIME_FORMAT")


def _format_datetime(dt: datetime | None) -> str:
    if not dt:
        return ""
    return date_format(timezone.localtime(dt), "Y-m-d H:i")


def format_datetime(dt: datetime | None) -> str:
    """Return *dt* formatted for UI output."""

    return _format_datetime(dt)


def _parse_runserver_port(command_line: str) -> int | None:
    """Extract the HTTP port from a runserver command line."""

    for pattern in (_RUNSERVER_PORT_PATTERN, _RUNSERVER_PORT_FLAG_PATTERN):
        match = pattern.search(command_line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _normalize_nginx_content(content: str) -> str:
    """Return *content* with trailing newlines removed for comparison."""

    return content.rstrip("\n")


def _resolve_external_websockets(default: bool = True) -> bool:
    try:
        from apps.nginx.models import SiteConfiguration

        config = SiteConfiguration.objects.filter(enabled=True).order_by("pk").first()
        if config is not None:
            return bool(config.external_websockets)
    except Exception:
        return default
    return default


def _build_nginx_report(
    *,
    base_dir: Path | None = None,
    site_path: Path | None = None,
    external_websockets: bool | None = None,
) -> dict[str, object]:
    """Return comparison data for the managed nginx configuration file."""

    resolved_base = Path(base_dir) if base_dir is not None else Path(settings.BASE_DIR)
    resolved_site_path = Path(site_path) if site_path is not None else _nginx_site_path()

    mode = _resolve_nginx_mode(resolved_base)
    port = _configured_backend_port(resolved_base)

    expected_content = ""
    expected_error = ""
    resolved_websockets = (
        _resolve_external_websockets()
        if external_websockets is None
        else external_websockets
    )
    try:
        expected_content = _normalize_nginx_content(
            generate_primary_config(mode, port, external_websockets=resolved_websockets)
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unable to generate expected nginx configuration")
        expected_error = str(exc)

    actual_content = ""
    actual_error = ""
    try:
        raw_content = resolved_site_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        actual_error = _("NGINX configuration file not found.")
    except OSError as exc:  # pragma: no cover - unexpected filesystem error
        actual_error = str(exc)
    else:
        actual_content = _normalize_nginx_content(raw_content)

    differs = bool(expected_error or actual_error or expected_content != actual_content)

    return {
        "expected_path": resolved_site_path,
        "actual_path": resolved_site_path,
        "expected_content": expected_content,
        "expected_error": expected_error,
        "actual_content": actual_content,
        "actual_error": actual_error,
        "differs": differs,
        "mode": mode,
        "port": port,
        "external_websockets": resolved_websockets,
    }


def _detect_runserver_process() -> tuple[bool, int | None]:
    """Return whether the dev server is running and the port if available."""

    try:
        result = subprocess.run(
            ["pgrep", "-af", "manage.py runserver"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, None
    except Exception:
        return False, None

    if result.returncode != 0:
        return False, None

    output = result.stdout.strip()
    if not output:
        return False, None

    port = None
    for line in output.splitlines():
        port = _parse_runserver_port(line)
        if port is not None:
            break

    if port is None:
        port = _configured_backend_port(Path(settings.BASE_DIR))

    return True, port


def _probe_ports(candidates: list[int]) -> tuple[bool, int | None]:
    """Attempt to connect to localhost on the provided ports."""

    for port in candidates:
        try:
            with closing(socket.create_connection(("localhost", port), timeout=0.25)):
                return True, port
        except OSError:
            continue
    return False, None


def _port_candidates(default_port: int) -> list[int]:
    """Return a prioritized list of ports to probe for the HTTP service."""

    candidates = [default_port]
    for port in (8000, 8888):
        if port not in candidates:
            candidates.append(port)
    return candidates


def _database_configurations() -> list[dict[str, str]]:
    """Return a normalized list of configured database connections."""

    databases: list[dict[str, str]] = []
    for alias, config in settings.DATABASES.items():
        engine = config.get("ENGINE", "")
        name = config.get("NAME", "")
        if engine is None:
            engine = ""
        if name is None:
            name = ""
        if isinstance(name, (os.PathLike, Path)):
            name = Path(name).as_posix()
        databases.append({
            "alias": alias,
            "engine": str(engine),
            "name": str(name),
        })
    databases.sort(key=lambda entry: entry["alias"].lower())
    return databases


def _build_system_fields(info: dict[str, object]) -> list[SystemField]:
    """Convert gathered system information into renderable rows."""

    fields: list[SystemField] = []

    def add_field(label: str, key: str, value: object, *, field_type: str = "text", visible: bool = True) -> None:
        if not visible:
            return
        fields.append(SystemField(label=label, sigil_key=key, value=value, field_type=field_type))

    add_field(_("Suite installed"), "INSTALLED", info.get("installed", False), field_type="boolean")
    add_field(_("Revision"), "REVISION", info.get("revision", ""))

    service_value = info.get("service") or _("not installed")
    add_field(_("Service"), "SERVICE", service_value)

    nginx_mode = info.get("mode", "")
    port = info.get("port", "")
    nginx_display = f"{nginx_mode} ({port})" if port else nginx_mode
    add_field(_("Nginx mode"), "NGINX_MODE", nginx_display)

    add_field(_("Node role"), "NODE_ROLE", info.get("role", ""))
    add_field(
        _("Display mode"),
        "DISPLAY_MODE",
        info.get("screen_mode", ""),
        visible=bool(info.get("screen_mode")),
    )

    add_field(_("Features"), "FEATURES", info.get("features", []), field_type="features")
    add_field(_("Running"), "RUNNING", info.get("running", False), field_type="boolean")
    add_field(
        _("Service status"),
        "SERVICE_STATUS",
        info.get("service_status", ""),
        visible=bool(info.get("service")),
    )

    add_field(_("Hostname"), "HOSTNAME", info.get("hostname", ""))

    ip_addresses: Iterable[str] = info.get("ip_addresses", [])  # type: ignore[assignment]
    add_field(_("IP addresses"), "IP_ADDRESSES", " ".join(ip_addresses))

    add_field(
        _("Databases"),
        "DATABASES",
        info.get("databases", []),
        field_type="databases",
    )

    add_field(
        _("Next version check"),
        "NEXT-VER-CHECK",
        info.get("auto_upgrade_next_check", ""),
    )

    return fields


def _gather_info(auto_upgrade_next_check: Callable[[], str]) -> dict:
    """Collect basic system information similar to status.sh."""

    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / ".locks"
    info: dict[str, object] = {}

    info["installed"] = (base_dir / ".venv").exists()
    info["revision"] = revision.get_revision()

    service_file = lock_dir / "service.lck"
    info["service"] = service_file.read_text().strip() if service_file.exists() else ""

    mode_file = lock_dir / "nginx_mode.lck"
    if mode_file.exists():
        try:
            raw_mode = mode_file.read_text().strip()
        except OSError:
            raw_mode = ""
    else:
        raw_mode = ""
    mode = raw_mode.lower() or "internal"
    info["mode"] = mode
    default_port = _configured_backend_port(base_dir)
    detected_port: int | None = None

    screen_file = lock_dir / "screen_mode.lck"
    info["screen_mode"] = (
        screen_file.read_text().strip() if screen_file.exists() else ""
    )

    info["role"] = getattr(settings, "NODE_ROLE", "Terminal")

    features: list[dict[str, object]] = []
    try:
        from apps.nodes.models import Node, NodeFeature
    except Exception:
        info["features"] = features
    else:
        feature_map: dict[str, dict[str, object]] = {}

        def _add_feature(feature: NodeFeature, flag: str) -> None:
            slug = getattr(feature, "slug", "") or ""
            if not slug:
                return
            display = (getattr(feature, "display", "") or "").strip()
            normalized = display or slug.replace("-", " ").title()
            entry = feature_map.setdefault(
                slug,
                {
                    "slug": slug,
                    "display": normalized,
                    "expected": False,
                    "actual": False,
                },
            )
            if display:
                entry["display"] = display
            entry[flag] = True

        try:
            expected_features = (
                NodeFeature.objects.filter(roles__name=info["role"]).only("slug", "display").distinct()
            )
        except Exception:
            expected_features = []
        try:
            for feature in expected_features:
                _add_feature(feature, "expected")
        except Exception:
            pass

        try:
            local_node = Node.get_local()
        except Exception:
            local_node = None

        actual_features = []
        if local_node:
            try:
                actual_features = list(local_node.features.only("slug", "display"))
            except Exception:
                actual_features = []

        try:
            for feature in actual_features:
                _add_feature(feature, "actual")
        except Exception:
            pass

        features = sorted(
            feature_map.values(),
            key=lambda item: str(item.get("display", "")).lower(),
        )
        info["features"] = features

    running = False
    service_status = ""
    service = info["service"]
    if service and shutil.which("systemctl"):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", str(service)],
                capture_output=True,
                text=True,
                check=False,
            )
            service_status = result.stdout.strip()
            running = service_status == "active"
        except Exception:
            pass
    else:
        process_running, process_port = _detect_runserver_process()
        if process_running:
            running = True
            detected_port = process_port

        if not running or detected_port is None:
            probe_running, probe_port = _probe_ports(_port_candidates(default_port))
            if probe_running:
                running = True
                if detected_port is None:
                    detected_port = probe_port

    info["running"] = running
    info["port"] = detected_port if detected_port is not None else default_port
    info["service_status"] = service_status

    try:
        hostname = socket.gethostname()
        ip_list = socket.gethostbyname_ex(hostname)[2]
    except Exception:
        hostname = ""
        ip_list = []
    info["hostname"] = hostname
    info["ip_addresses"] = ip_list

    info["databases"] = _database_configurations()
    info["auto_upgrade_next_check"] = auto_upgrade_next_check()

    return info


def _system_boot_time(now: datetime | None = None) -> datetime | None:
    """Return the host boot time if it can be determined."""

    current_time = now or timezone.now()
    try:
        import psutil
    except Exception:
        return None

    try:
        boot_timestamp = float(psutil.boot_time())
    except Exception:
        return None

    if not boot_timestamp:
        return None

    boot_time = datetime.fromtimestamp(boot_timestamp, tz=datetime_timezone.utc)
    if boot_time > current_time:
        return None

    return boot_time


def _suite_uptime_details() -> dict[str, object]:
    """Return structured uptime information for the running suite if possible."""

    now = timezone.now()
    lock_info = _suite_uptime_lock_info(now=now)
    boot_time = _system_boot_time(now)
    lock_start = lock_info.get("started_at")

    if lock_start and boot_time and lock_start < boot_time:
        return {
            "available": False,
            "boot_time": boot_time,
            "boot_time_label": _format_datetime(boot_time),
            "lock_started_at": lock_start,
        }

    if lock_info.get("fresh") and isinstance(lock_start, datetime):
        uptime_label = timesince(lock_start, now)
        return {
            "uptime": uptime_label,
            "boot_time": lock_start,
            "boot_time_label": _format_datetime(lock_start),
            "available": True,
        }

    if lock_info.get("exists"):
        if boot_time:
            uptime_label = timesince(boot_time, now)
            return {
                "uptime": uptime_label,
                "boot_time": boot_time,
                "boot_time_label": _format_datetime(boot_time),
                "lock_started_at": lock_start,
                "available": True,
            }
        return {"available": False, "boot_time": boot_time}

    if boot_time:
        uptime_label = timesince(boot_time, now)
        return {
            "uptime": uptime_label,
            "boot_time": boot_time,
            "boot_time_label": _format_datetime(boot_time),
            "available": True,
        }

    return {}


def _suite_uptime() -> str:
    """Return a human-readable uptime for the running suite when possible."""

    return str(_suite_uptime_details().get("uptime", ""))


def _suite_offline_period(now: datetime) -> tuple[datetime, datetime] | None:
    """Return a downtime window when the lock predates the current boot."""

    lock_info = _suite_uptime_lock_info(now=now)
    lock_start = lock_info.get("started_at")
    boot_time = _system_boot_time(now)

    if boot_time and isinstance(lock_start, datetime) and lock_start < boot_time:
        return boot_time, now

    return None


def suite_offline_period(now: datetime) -> tuple[datetime, datetime] | None:
    """Return a downtime window when the lock predates the current boot."""

    return _suite_offline_period(now)


def _parse_last_history_line(line: str) -> dict[str, object] | None:
    """Parse a single ``last -x -F`` line for shutdown or reboot entries."""

    if not line or line.startswith("wtmp begins"):
        return None

    tokens = line.split()
    if not tokens or tokens[0] not in {"reboot", "shutdown"}:
        return None

    try:
        start_index = next(index for index, token in enumerate(tokens) if token in _DAY_NAMES)
    except StopIteration:
        return None

    if start_index + 4 >= len(tokens):
        return None

    start_text = " ".join(tokens[start_index : start_index + 5])
    try:
        start_dt = datetime.strptime(start_text, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    start_dt = timezone.make_aware(start_dt, timezone.get_current_timezone())

    dash_index = None
    for index in range(start_index + 5, len(tokens)):
        if tokens[index] == "-":
            dash_index = index
            break

    end_dt = None
    if dash_index is not None and dash_index + 5 < len(tokens):
        end_text = " ".join(tokens[dash_index + 1 : dash_index + 6])
        try:
            end_dt = datetime.strptime(end_text, "%a %b %d %H:%M:%S %Y")
        except ValueError:
            end_dt = None
        else:
            end_dt = timezone.make_aware(end_dt, timezone.get_current_timezone())

    return {"type": tokens[0], "start": start_dt, "end": end_dt}


def _load_shutdown_periods() -> tuple[list[tuple[datetime, datetime | None]], str | None]:
    """Return shutdown periods parsed from ``last -x -F`` output."""

    try:
        result = subprocess.run(
            ["last", "-x", "-F"], capture_output=True, check=False, text=True
        )
    except FileNotFoundError:
        return [], _("The `last` command is not available on this node.")

    if result.returncode not in (0, 1):
        return [], _("Unable to read uptime history from the system log.")

    shutdown_periods: list[tuple[datetime, datetime | None]] = []
    for line in result.stdout.splitlines():
        record = _parse_last_history_line(line.strip())
        if not record or record["type"] != "shutdown":
            continue
        start = record.get("start")
        end = record.get("end")
        if isinstance(start, datetime):
            shutdown_periods.append((start, end if isinstance(end, datetime) else None))

    return shutdown_periods, None


def load_shutdown_periods() -> tuple[list[tuple[datetime, datetime | None]], str | None]:
    """Return shutdown periods parsed from ``last -x -F`` output."""

    return _load_shutdown_periods()


def _parse_startup_report_entry(line: str) -> dict[str, object] | None:
    text = line.strip()
    if not text:
        return None

    parts = text.split("\t")
    timestamp_raw = parts[0] if parts else ""
    script = parts[1] if len(parts) > 1 else ""
    event = parts[2] if len(parts) > 2 else ""
    detail = parts[3] if len(parts) > 3 else ""

    parsed_timestamp = None
    if timestamp_raw:
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp_raw)
            if timezone.is_naive(parsed_timestamp):
                parsed_timestamp = timezone.make_aware(
                    parsed_timestamp, timezone.get_current_timezone()
                )
        except ValueError:
            parsed_timestamp = None

    timestamp_label = _format_datetime(parsed_timestamp) if parsed_timestamp else timestamp_raw

    return {
        "timestamp": parsed_timestamp,
        "timestamp_raw": timestamp_raw,
        "timestamp_label": timestamp_label or timestamp_raw,
        "script": script,
        "event": event,
        "detail": detail,
        "raw": text,
    }


def _read_startup_report(
    *, limit: int | None = None, base_dir: Path | None = None
) -> dict[str, object]:
    normalized_limit = limit if limit is None or limit > 0 else None
    log_path = _startup_report_log_path(base_dir)
    lines: deque[str] = deque(maxlen=normalized_limit)

    try:
        with log_path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                lines.append(raw_line.rstrip("\n"))
    except FileNotFoundError:
        return {
            "entries": [],
            "log_path": log_path,
            "missing": True,
            "error": _("Startup report log does not exist yet."),
            "limit": normalized_limit,
            "clock_warning": None,
        }
    except OSError as exc:
        return {
            "entries": [],
            "log_path": log_path,
            "missing": False,
            "error": str(exc),
            "limit": normalized_limit,
            "clock_warning": None,
        }

    parsed_entries = [
        entry for raw_line in lines if (entry := _parse_startup_report_entry(raw_line))
    ]
    parsed_entries.reverse()

    reference_time = _startup_report_reference_time(log_path) or timezone.now()
    clock_warning = None
    for entry in parsed_entries:
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue

        delta = timestamp - reference_time
        absolute_delta = delta if delta >= timedelta(0) else -delta
        if absolute_delta <= STARTUP_CLOCK_DRIFT_THRESHOLD:
            break

        offset_label = timesince(
            reference_time - absolute_delta, reference_time
        )
        direction = _("ahead") if delta > timedelta(0) else _("behind")
        clock_warning = _(
            "Startup timestamps appear %(offset)s %(direction)s of the current system time. "
            "Check the suite clock or NTP configuration."
        ) % {"offset": offset_label, "direction": direction}
        break

    return {
        "entries": parsed_entries,
        "log_path": log_path,
        "missing": False,
        "error": None,
        "limit": normalized_limit,
        "clock_warning": clock_warning,
    }


def _merge_shutdown_periods(periods: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    normalized: list[tuple[datetime, datetime]] = []
    for start, end in periods:
        if end < start:
            continue
        normalized.append((start, end))

    normalized.sort(key=lambda value: value[0])
    merged: list[tuple[datetime, datetime]] = []
    for start, end in normalized:
        if not merged:
            merged.append((start, end))
            continue
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _build_uptime_segments(
    *, window_start: datetime, window_end: datetime, shutdown_periods: list[tuple[datetime, datetime]]
) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    if window_end <= window_start:
        return segments

    merged_periods = _merge_shutdown_periods(shutdown_periods)
    cursor = window_start
    for down_start, down_end in merged_periods:
        if down_end <= window_start or down_start >= window_end:
            continue
        if cursor < down_start:
            up_end = min(down_start, window_end)
            duration = up_end - cursor
            segments.append(
                {
                    "status": "up",
                    "start": cursor,
                    "end": up_end,
                    "duration": duration,
                }
            )
        segment_start = max(down_start, window_start)
        segment_end = min(down_end, window_end)
        duration = segment_end - segment_start
        segments.append(
            {
                "status": "down",
                "start": segment_start,
                "end": segment_end,
                "duration": duration,
            }
        )
        cursor = segment_end
    if cursor < window_end:
        duration = window_end - cursor
        segments.append(
            {
                "status": "up",
                "start": cursor,
                "end": window_end,
                "duration": duration,
            }
        )

    return segments


def build_uptime_segments(
    *, window_start: datetime, window_end: datetime, shutdown_periods: list[tuple[datetime, datetime]]
) -> list[dict[str, object]]:
    """Return uptime/downtime segments for the given window."""

    return _build_uptime_segments(
        window_start=window_start,
        window_end=window_end,
        shutdown_periods=shutdown_periods,
    )


def _serialize_segments(segments: list[dict[str, object]], *, window_duration: float) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for segment in segments:
        start = segment["start"]
        end = segment["end"]
        duration: timedelta = segment["duration"]
        duration_seconds = max(duration.total_seconds(), 0.0)
        width = 0.0
        if window_duration > 0:
            width = (duration_seconds / window_duration) * 100
        serialized.append(
            {
                "status": segment["status"],
                "start": start,
                "end": end,
                "width": width,
                "duration": duration,
                "duration_label": timesince(start, end),
                "label": _(
                    "%(status)s from %(start)s to %(end)s"
                )
                % {
                    "status": _(segment["status"] == "up" and "Up" or "Down"),
                    "start": _format_datetime(start),
                    "end": _format_datetime(end),
                },
            }
        )
    return serialized


def _build_uptime_report(*, now: datetime | None = None) -> dict[str, object]:
    current_time = now or timezone.now()
    raw_periods, error = _load_shutdown_periods()
    shutdown_periods = []
    for start, end in raw_periods:
        normalized_end = end or current_time
        if normalized_end < start:
            continue
        shutdown_periods.append((start, normalized_end))

    offline_period = _suite_offline_period(current_time)
    if offline_period:
        shutdown_periods.append(offline_period)

    windows = [
        (_("Last 24 hours"), current_time - timedelta(hours=24)),
        (_("Last 7 days"), current_time - timedelta(days=7)),
        (_("Last 30 days"), current_time - timedelta(days=30)),
    ]

    report_windows: list[dict[str, object]] = []
    for label, start in windows:
        window_duration = (current_time - start).total_seconds()
        segments = _build_uptime_segments(
            window_start=start, window_end=current_time, shutdown_periods=shutdown_periods
        )
        serialized_segments = _serialize_segments(segments, window_duration=window_duration)
        uptime_seconds = sum(
            segment["duration"].total_seconds()
            for segment in serialized_segments
            if segment["status"] == "up"
        )
        downtime_seconds = max(window_duration - uptime_seconds, 0.0)
        uptime_percent = 0.0
        downtime_percent = 0.0
        if window_duration > 0:
            uptime_percent = (uptime_seconds / window_duration) * 100
            downtime_percent = (downtime_seconds / window_duration) * 100

        report_windows.append(
            {
                "label": label,
                "start": start,
                "end": current_time,
                "segments": serialized_segments,
                "uptime_percent": round(uptime_percent, 1),
                "downtime_percent": round(downtime_percent, 1),
                "downtime_events": [
                    {
                        "start": _format_datetime(segment["start"]),
                        "end": _format_datetime(segment["end"]),
                        "duration": timesince(segment["start"], segment["end"]),
                    }
                    for segment in serialized_segments
                    if segment["status"] == "down"
                ],
            }
        )

    suite_details = _suite_uptime_details()
    suite_info = {
        "uptime": suite_details.get("uptime", ""),
        "boot_time": suite_details.get("boot_time"),
        "boot_time_label": suite_details.get("boot_time_label", ""),
        "available": bool(suite_details.get("available") or suite_details.get("uptime")),
    }

    return {
        "generated_at": current_time,
        "windows": report_windows,
        "error": error,
        "suite": suite_info,
    }


def _service_docs_url(doc: str) -> str:
    try:
        return reverse("docs:docs-document", args=[doc])
    except NoReverseMatch:
        return ""


def _configured_service_units(base_dir: Path) -> list[dict[str, object]]:
    """Return service units configured for this instance."""

    from apps.celery.utils import is_celery_enabled

    lock_dir = base_dir / ".locks"
    service_file = lock_dir / "service.lck"
    systemd_services_file = lock_dir / "systemd_services.lck"

    try:
        service_name = service_file.read_text(encoding="utf-8").strip()
    except OSError:
        service_name = ""

    try:
        systemd_units = systemd_services_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        systemd_units = []

    service_units: list[dict[str, object]] = []

    def _normalize_unit(unit_name: str) -> tuple[str, str]:
        normalized = unit_name.strip()
        unit_display = normalized
        unit = normalized
        if normalized.endswith(".service"):
            unit = normalized.removesuffix(".service")
        else:
            unit_display = f"{normalized}.service"
        return unit, unit_display

    def _add_unit(
        unit_name: str,
        *,
        key: str | None = None,
        label: str | None = None,
        configured: bool = True,
        docs_url: str = "",
        pid_file: str = "",
    ) -> None:
        normalized = unit_name.strip()
        if not normalized:
            return

        unit, unit_display = _normalize_unit(normalized)
        for existing_unit in service_units:
            if existing_unit["unit_display"] == unit_display:
                if key and not existing_unit.get("key"):
                    existing_unit["key"] = key
                existing_unit["label"] = label or existing_unit["label"]
                existing_unit["configured"] = configured
                existing_unit["docs_url"] = docs_url
                if pid_file and not existing_unit.get("pid_file"):
                    existing_unit["pid_file"] = pid_file
                return
        service_units.append(
            {
                "key": key or "",
                "label": label or normalized,
                "unit": unit,
                "unit_display": unit_display,
                "configured": configured,
                "docs_url": docs_url,
                "pid_file": pid_file or "",
            }
        )

    service_name_placeholder = service_name or "SERVICE_NAME"
    celery_enabled = is_celery_enabled(lock_dir / "celery.lck")
    lcd_enabled = lcd_feature_enabled(lock_dir)
    rfid_enabled = rfid_service_enabled(lock_dir)

    for spec in SERVICE_REPORT_DEFINITIONS:
        unit_name = spec["unit_template"].format(service=service_name_placeholder)
        if not service_name:
            configured = False
        elif spec["key"] == "suite":
            configured = True
        elif spec["key"] in {"celery-worker", "celery-beat"}:
            configured = celery_enabled
        elif spec["key"] == "lcd-screen":
            configured = lcd_enabled
        elif spec["key"] == "rfid-service":
            configured = rfid_enabled
        else:
            configured = False

        _add_unit(
            unit_name,
            key=spec.get("key"),
            label=str(spec["label"]),
            configured=configured,
            docs_url=_service_docs_url(spec["docs"]),
            pid_file=spec.get("pid_file", ""),
        )

    base_label_map: dict[str, str] = {}
    docs_url_map: dict[str, str] = {}
    if service_name:
        base_label_map = {
            f"{service_name}.service": str(_("Suite service")),
            f"celery-{service_name}.service": str(_("Celery worker")),
            f"celery-beat-{service_name}.service": str(_("Celery beat")),
            f"lcd-{service_name}.service": str(_("LCD screen")),
            f"rfid-{service_name}.service": str(_("RFID scanner service")),
        }
        for spec in SERVICE_REPORT_DEFINITIONS:
            docs_url_map[
                spec["unit_template"].format(service=service_name)
            ] = _service_docs_url(spec["docs"])

    for unit_name in systemd_units:
        normalized = unit_name.strip()
        _add_unit(
            normalized,
            label=base_label_map.get(normalized),
            configured=True,
            docs_url=docs_url_map.get(normalized, ""),
        )

    return service_units


def _systemd_unit_status(unit: str, command: list[str] | None = None) -> dict[str, object]:
    """Return the systemd status for a unit, handling missing commands gracefully."""

    command = command if command is not None else _systemctl_command()
    if not command:
        return {
            "status": str(_("Unavailable")),
            "enabled": "",
            "missing": False,
        }

    try:
        active_result = subprocess.run(
            [*command, "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return {
            "status": str(_("Unknown")),
            "enabled": "",
            "missing": False,
        }

    status_output = (active_result.stdout or active_result.stderr).strip()
    status = status_output or str(_("unknown"))
    missing = active_result.returncode == 4

    enabled_state = ""
    if not missing:
        try:
            enabled_result = subprocess.run(
                [*command, "is-enabled", unit],
                capture_output=True,
                text=True,
                check=False,
            )
            enabled_state = (enabled_result.stdout or enabled_result.stderr).strip()
        except Exception:
            enabled_state = ""

    return {
        "status": status,
        "enabled": enabled_state,
        "missing": missing,
    }


def _embedded_service_status(lock_dir: Path, pid_file: str) -> dict[str, object]:
    running = _pid_file_running(lock_dir / pid_file)
    status_label = _("active (embedded)") if running else _("inactive (embedded)")
    return {
        "status": str(status_label),
        "enabled": str(_("Embedded")),
        "missing": False,
    }


def _build_services_report() -> dict[str, object]:
    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / ".locks"
    configured_units = _configured_service_units(base_dir)
    command = _systemctl_command()
    service_mode = _read_service_mode(lock_dir)
    embedded_mode = service_mode == "embedded"

    services: list[dict[str, object]] = []
    for unit in configured_units:
        if unit.get("configured"):
            pid_file = unit.get("pid_file", "")
            if embedded_mode and pid_file:
                status_info = _embedded_service_status(lock_dir, pid_file)
            else:
                status_info = _systemd_unit_status(unit["unit"], command=command)
        else:
            status_info = {
                "status": str(_("Not configured")),
                "enabled": "",
                "missing": False,
            }
        services.append({**unit, **status_info})

    return {
        "services": services,
        "systemd_available": bool(command),
        "has_services": bool(configured_units),
    }
