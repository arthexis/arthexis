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

from apps.core.systemctl import _systemctl_command
from apps.nginx.renderers import generate_primary_config
from apps.services.lifecycle import build_lifecycle_service_units
from utils import revision

from ..filesystem import (
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

    add_field(_("Node Features"), "FEATURES", info.get("features", []), field_type="features")
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



# Re-export split helpers from dedicated modules.
from .formatting import _format_datetime, _format_timestamp, format_datetime
from .network_probe import (
    _build_nginx_report,
    _detect_runserver_process,
    _port_candidates,
    _probe_ports,
)
from .services import _build_services_report, _configured_service_units, _systemd_unit_status
from .uptime import (
    _build_uptime_report,
    _build_uptime_segments,
    _load_shutdown_periods,
    _suite_offline_period,
    _suite_uptime,
    build_uptime_segments,
    load_shutdown_periods,
    suite_offline_period,
)
