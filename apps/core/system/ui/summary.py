from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
import logging
import os
import shutil
import socket
import subprocess
from typing import Callable, Iterable

from django.conf import settings
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from apps.nginx.renderers import generate_primary_config
from utils import revision

from ..filesystem import (
    _configured_backend_port,
    _nginx_site_path,
    _resolve_nginx_mode,
    _suite_uptime_lock_info,
)
from .formatters import format_datetime
from .runtime import _detect_runserver_process, _port_candidates, _probe_ports


logger = logging.getLogger(__name__)

_DAY_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


@dataclass(frozen=True)
class SystemField:
    """Metadata describing a single entry on the system admin page."""

    label: str
    sigil_key: str
    value: object
    field_type: str = "text"

    @property
    def sigil(self) -> str:
        """Return the fully-qualified sigil key."""

        return f"SYS.{self.sigil_key}"


def _normalize_nginx_content(content: str) -> str:
    """Return *content* with trailing newlines removed for comparison."""

    return content.rstrip("\n")


def _resolve_external_websockets(default: bool = True) -> bool:
    """Return the configured external websockets flag with a fallback."""

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

    def add_field(
        label: str,
        key: str,
        value: object,
        *,
        field_type: str = "text",
        visible: bool = True,
    ) -> None:
        """Add a field to the system info list when visible."""

        if not visible:
            return
        fields.append(
            SystemField(label=label, sigil_key=key, value=value, field_type=field_type)
        )

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
            """Append a feature description to the info payload."""

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
            "boot_time_label": format_datetime(boot_time),
            "lock_started_at": lock_start,
        }

    if lock_info.get("fresh") and isinstance(lock_start, datetime):
        uptime_label = timesince(lock_start, now)
        return {
            "uptime": uptime_label,
            "boot_time": lock_start,
            "boot_time_label": format_datetime(lock_start),
            "available": True,
        }

    if lock_info.get("exists"):
        if boot_time:
            uptime_label = timesince(boot_time, now)
            return {
                "uptime": uptime_label,
                "boot_time": boot_time,
                "boot_time_label": format_datetime(boot_time),
                "lock_started_at": lock_start,
                "available": True,
            }
        return {"available": False, "boot_time": boot_time}

    if boot_time:
        uptime_label = timesince(boot_time, now)
        return {
            "uptime": uptime_label,
            "boot_time": boot_time,
            "boot_time_label": format_datetime(boot_time),
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


def _merge_shutdown_periods(periods: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping shutdown windows."""

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
    """Build uptime segments for the requested time window."""

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
    """Serialize uptime segments for rendering."""

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
                    "start": format_datetime(start),
                    "end": format_datetime(end),
                },
            }
        )
    return serialized


def _build_uptime_report(*, now: datetime | None = None) -> dict[str, object]:
    """Return uptime summary information and segment details."""

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
                        "start": format_datetime(segment["start"]),
                        "end": format_datetime(segment["end"]),
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
