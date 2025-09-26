from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import socket
import subprocess
import shutil
from typing import Callable, Iterable, Optional

from django.conf import settings
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from core.auto_upgrade import AUTO_UPGRADE_TASK_NAME
from utils import revision


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


def _format_timestamp(dt: datetime | None) -> str:
    """Return ``dt`` formatted using the active ``DATETIME_FORMAT``."""

    if dt is None:
        return ""
    try:
        localized = timezone.localtime(dt)
    except Exception:
        localized = dt
    return date_format(localized, "DATETIME_FORMAT")


def _auto_upgrade_next_check() -> str:
    """Return the human-readable timestamp for the next auto-upgrade check."""

    try:  # pragma: no cover - optional dependency failures
        from django_celery_beat.models import PeriodicTask
    except Exception:
        return ""

    try:
        task = (
            PeriodicTask.objects.select_related(
                "interval", "crontab", "solar", "clocked"
            )
            .only("enabled", "last_run_at", "start_time", "name")
            .get(name=AUTO_UPGRADE_TASK_NAME)
        )
    except PeriodicTask.DoesNotExist:
        return ""
    except Exception:  # pragma: no cover - database unavailable
        return ""

    if not task.enabled:
        return str(_("Disabled"))

    schedule = task.schedule
    if schedule is None:
        return ""

    now = schedule.maybe_make_aware(schedule.now())

    start_time = task.start_time
    if start_time is not None:
        try:
            candidate_start = schedule.maybe_make_aware(start_time)
        except Exception:
            candidate_start = (
                timezone.make_aware(start_time)
                if timezone.is_naive(start_time)
                else start_time
            )
        if candidate_start and candidate_start > now:
            return _format_timestamp(candidate_start)

    last_run_at = task.last_run_at
    if last_run_at is not None:
        try:
            reference = schedule.maybe_make_aware(last_run_at)
        except Exception:
            reference = (
                timezone.make_aware(last_run_at)
                if timezone.is_naive(last_run_at)
                else last_run_at
            )
    else:
        reference = now

    try:
        remaining = schedule.remaining_estimate(reference)
    except Exception:
        return ""

    next_run = now + remaining
    return _format_timestamp(next_run)


def _resolve_auto_upgrade_namespace(key: str) -> str | None:
    """Resolve sigils within the legacy ``AUTO-UPGRADE`` namespace."""

    normalized = key.replace("-", "_").upper()
    if normalized == "NEXT_CHECK":
        return _auto_upgrade_next_check()
    return None


_SYSTEM_SIGIL_NAMESPACES: dict[str, Callable[[str], Optional[str]]] = {
    "AUTO_UPGRADE": _resolve_auto_upgrade_namespace,
}


def resolve_system_namespace_value(key: str) -> str | None:
    """Resolve dot-notation sigils mapped to dynamic ``SYS`` namespaces."""

    if not key:
        return None
    normalized_key = key.replace("-", "_").upper()
    if normalized_key == "NEXT_VER_CHECK":
        return _auto_upgrade_next_check()
    namespace, _, remainder = key.partition(".")
    if not remainder:
        return None
    normalized = namespace.replace("-", "_").upper()
    handler = _SYSTEM_SIGIL_NAMESPACES.get(normalized)
    if not handler:
        return None
    return handler(remainder)


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


def _export_field_value(field: SystemField) -> str:
    """Serialize a ``SystemField`` value for sigil resolution."""

    if field.field_type in {"features", "databases"}:
        return json.dumps(field.value)
    if field.field_type == "boolean":
        return "True" if field.value else "False"
    if field.value is None:
        return ""
    return str(field.value)


def get_system_sigil_values() -> dict[str, str]:
    """Expose system information in a format suitable for sigil lookups."""

    info = _gather_info()
    values: dict[str, str] = {}
    for field in _build_system_fields(info):
        exported = _export_field_value(field)
        raw_key = (field.sigil_key or "").strip()
        if not raw_key:
            continue
        variants = {
            raw_key.upper(),
            raw_key.replace("-", "_").upper(),
        }
        for variant in variants:
            values[variant] = exported
    return values


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
        port = 8000

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


def _gather_info() -> dict:
    """Collect basic system information similar to status.sh."""
    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / "locks"
    info: dict[str, object] = {}

    info["installed"] = (base_dir / ".venv").exists()
    info["revision"] = revision.get_revision()

    service_file = lock_dir / "service.lck"
    info["service"] = service_file.read_text().strip() if service_file.exists() else ""

    mode_file = lock_dir / "nginx_mode.lck"
    mode = mode_file.read_text().strip() if mode_file.exists() else "internal"
    info["mode"] = mode
    default_port = 8000 if mode == "public" else 8888
    detected_port: int | None = None

    screen_file = lock_dir / "screen_mode.lck"
    info["screen_mode"] = (
        screen_file.read_text().strip() if screen_file.exists() else ""
    )

    # Use settings.NODE_ROLE as the single source of truth for the node role.
    info["role"] = getattr(settings, "NODE_ROLE", "Terminal")

    features: list[dict[str, object]] = []
    try:
        from nodes.models import Node, NodeFeature
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
    info["auto_upgrade_next_check"] = _auto_upgrade_next_check()

    return info


def _system_view(request):
    info = _gather_info()

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("System"),
            "info": info,
            "system_fields": _build_system_fields(info),
        }
    )
    return TemplateResponse(request, "admin/system.html", context)


def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
