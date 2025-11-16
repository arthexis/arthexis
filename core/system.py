from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import json
import re
import socket
import subprocess
import shutil
import logging
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.forms import modelformset_factory
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _, ngettext

from django.db import DatabaseError

from core.auto_upgrade import (
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    ensure_auto_upgrade_periodic_task,
)
from core.auto_upgrade_failover import clear_failover_lock, read_failover_status
from core.release import (
    _git_authentication_missing,
    _git_remote_url,
    _manager_git_credentials,
    _remote_with_credentials,
)
from core.tasks import check_github_updates
from core.models import Todo
from utils import revision
from core import changelog


AUTO_UPGRADE_LOCK_NAME = "auto_upgrade.lck"
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
AUTO_UPGRADE_LOG_NAME = "auto-upgrade.log"
AUTO_UPGRADE_LOG_LIMIT = 30


UPGRADE_CHANNEL_CHOICES: dict[str, dict[str, object]] = {
    "normal": {"override": None, "label": _("Normal")},
    "latest": {"override": "latest", "label": _("Latest")},
    "stable": {"override": "stable", "label": _("Stable")},
}


logger = logging.getLogger(__name__)


def _github_repo_path(remote_url: str | None) -> str:
    """Return the ``owner/repo`` path for a GitHub *remote_url* if possible."""

    if not remote_url:
        return ""

    normalized = remote_url.strip()
    if not normalized:
        return ""

    path = ""
    if normalized.startswith("git@"):
        host, _, remainder = normalized.partition(":")
        if "github.com" not in host.lower():
            return ""
        path = remainder
    else:
        parsed = urlparse(normalized)
        if "github.com" not in parsed.netloc.lower():
            return ""
        path = parsed.path

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]

    if not path:
        return ""

    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return ""

    owner, repo = segments[-2], segments[-1]
    return f"{owner}/{repo}"


@lru_cache()
def _github_commit_url_base() -> str:
    """Return the GitHub commit URL template for the configured repository."""

    try:
        remote_url = _git_remote_url()
    except FileNotFoundError:  # pragma: no cover - depends on environment setup
        logger.debug("Skipping GitHub commit URL generation; git executable not found")
        remote_url = None

    repo_path = _github_repo_path(remote_url)
    if not repo_path:
        return ""
    return f"https://github.com/{repo_path}/commit/{{sha}}"


def _github_commit_url(sha: str) -> str:
    """Return the GitHub commit URL for *sha* when available."""

    base = _github_commit_url_base()
    clean_sha = (sha or "").strip()
    if not base or not clean_sha:
        return ""
    return base.replace("{sha}", clean_sha)


def _auto_upgrade_mode_file(base_dir: Path) -> Path:
    return base_dir / "locks" / AUTO_UPGRADE_LOCK_NAME


def _auto_upgrade_skip_file(base_dir: Path) -> Path:
    return base_dir / "locks" / AUTO_UPGRADE_SKIP_LOCK_NAME


def _auto_upgrade_log_file(base_dir: Path) -> Path:
    return base_dir / "logs" / AUTO_UPGRADE_LOG_NAME


def _clear_auto_upgrade_skip_revisions(base_dir: Path) -> None:
    """Remove recorded skip revisions so future upgrade attempts proceed."""

    skip_file = _auto_upgrade_skip_file(base_dir)
    try:
        skip_file.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to remove auto-upgrade skip lockfile: %s", exc)


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


def _read_auto_upgrade_mode(base_dir: Path) -> dict[str, object]:
    """Return metadata describing the configured auto-upgrade mode."""

    mode_file = _auto_upgrade_mode_file(base_dir)
    info: dict[str, object] = {
        "mode": "version",
        "enabled": False,
        "lock_exists": mode_file.exists(),
        "read_error": False,
    }

    if not info["lock_exists"]:
        return info

    info["enabled"] = True

    try:
        raw_value = mode_file.read_text(encoding="utf-8").strip()
    except OSError:
        info["read_error"] = True
        return info

    mode = raw_value or "version"
    info["mode"] = mode
    info["enabled"] = True
    return info


def _load_auto_upgrade_skip_revisions(base_dir: Path) -> list[str]:
    """Return a sorted list of revisions blocked from auto-upgrade."""

    skip_file = _auto_upgrade_skip_file(base_dir)
    try:
        lines = skip_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        return []

    revisions = {line.strip() for line in lines if line.strip()}
    return sorted(revisions)


def _parse_log_timestamp(value: str) -> datetime | None:
    """Return a ``datetime`` parsed from ``value`` if it appears ISO formatted."""

    if not value:
        return None

    candidate = value.strip()
    if not candidate:
        return None

    if candidate[-1] in {"Z", "z"}:
        candidate = f"{candidate[:-1]}+00:00"

    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _load_auto_upgrade_log_entries(
    base_dir: Path, *, limit: int = AUTO_UPGRADE_LOG_LIMIT
) -> dict[str, object]:
    """Return the most recent auto-upgrade log entries."""

    log_file = _auto_upgrade_log_file(base_dir)
    result: dict[str, object] = {
        "path": log_file,
        "entries": [],
        "error": "",
    }

    try:
        with log_file.open("r", encoding="utf-8") as handle:
            lines = deque((line.rstrip("\n") for line in handle), maxlen=limit)
    except FileNotFoundError:
        return result
    except OSError:
        result["error"] = str(
            _("The auto-upgrade log could not be read."))
        return result

    entries: list[dict[str, str]] = []
    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line:
            continue
        timestamp_str, _, message = line.partition(" ")
        message = message.strip()
        timestamp = _parse_log_timestamp(timestamp_str)
        if not message:
            message = timestamp_str
        if timestamp is not None:
            timestamp_display = _format_timestamp(timestamp)
        else:
            timestamp_display = timestamp_str
        entries.append({
            "timestamp": timestamp_display,
            "message": message,
        })

    result["entries"] = entries
    return result


def _reverse_admin_url(route: str, *args) -> str:
    """Return ``reverse(route, args=args)`` while ignoring missing routes."""

    try:
        return reverse(route, args=args)
    except NoReverseMatch:
        return ""


def _get_auto_upgrade_periodic_task():
    """Return the configured auto-upgrade periodic task, if available."""

    try:  # pragma: no cover - optional dependency failures
        from django_celery_beat.models import PeriodicTask
    except Exception:
        return None, False, str(_("django-celery-beat is not installed or configured."))

    def _query():
        return (
            PeriodicTask.objects.select_related(
                "interval",
                "crontab",
                "solar",
                "clocked",
            )
            .only(
                "enabled",
                "last_run_at",
                "start_time",
                "one_off",
                "total_run_count",
                "queue",
                "expires",
                "task",
                "name",
                "description",
                "id",
                "interval_id",
                "crontab_id",
                "solar_id",
                "clocked_id",
            )
            .get(name=AUTO_UPGRADE_TASK_NAME)
        )

    for attempt in range(2):
        try:
            task = _query()
        except PeriodicTask.DoesNotExist:
            if attempt:
                return None, True, ""
            try:
                ensure_auto_upgrade_periodic_task()
            except Exception:  # pragma: no cover - repair attempt failed
                logger.exception("Unable to recreate auto-upgrade periodic task")
                return None, False, str(_("Auto-upgrade schedule could not be loaded."))
        except DatabaseError:
            logger.exception("Error loading auto-upgrade periodic task")
            if attempt:
                return None, False, str(_("Auto-upgrade schedule could not be loaded."))
            try:
                ensure_auto_upgrade_periodic_task()
            except Exception:  # pragma: no cover - repair attempt failed
                logger.exception("Unable to recreate auto-upgrade periodic task")
                return None, False, str(_("Auto-upgrade schedule could not be loaded."))
        except Exception:
            logger.exception("Unexpected failure while loading auto-upgrade task")
            return None, False, str(_("Auto-upgrade schedule could not be loaded."))
        else:
            return task, True, ""

    return None, True, ""


def _resolve_auto_upgrade_schedule_links(task) -> dict[str, str]:
    """Return admin URLs related to *task* when available."""

    links = {
        "task_admin_url": "",
        "config_admin_url": "",
        "config_type": "",
    }

    if not task:
        return links

    pk = getattr(task, "pk", None)
    if pk:
        links["task_admin_url"] = _reverse_admin_url(
            "admin:django_celery_beat_periodictask_change", pk
        )

    schedule_routes = (
        ("interval", "admin:django_celery_beat_intervalschedule_change"),
        ("crontab", "admin:django_celery_beat_crontabschedule_change"),
        ("solar", "admin:django_celery_beat_solarschedule_change"),
        ("clocked", "admin:django_celery_beat_clockedschedule_change"),
    )
    for attr, route in schedule_routes:
        related_id = getattr(task, f"{attr}_id", None)
        if related_id:
            links["config_admin_url"] = _reverse_admin_url(route, related_id)
            links["config_type"] = attr
            break

    return links


def _load_auto_upgrade_schedule() -> dict[str, object]:
    """Return normalized auto-upgrade scheduling metadata."""

    task, available, error = _get_auto_upgrade_periodic_task()
    info: dict[str, object] = {
        "available": available,
        "configured": bool(task),
        "enabled": getattr(task, "enabled", False) if task else False,
        "one_off": getattr(task, "one_off", False) if task else False,
        "queue": getattr(task, "queue", "") or "",
        "schedule": "",
        "start_time": "",
        "last_run_at": "",
        "next_run": "",
        "total_run_count": 0,
        "description": getattr(task, "description", "") or "",
        "expires": "",
        "task": getattr(task, "task", "") or "",
        "name": getattr(task, "name", AUTO_UPGRADE_TASK_NAME) or AUTO_UPGRADE_TASK_NAME,
        "error": error,
        "task_admin_url": "",
        "config_admin_url": "",
        "config_type": "",
    }

    if not task:
        return info

    links = _resolve_auto_upgrade_schedule_links(task)
    info.update(links)

    info["start_time"] = _format_timestamp(getattr(task, "start_time", None))
    info["last_run_at"] = _format_timestamp(getattr(task, "last_run_at", None))
    info["expires"] = _format_timestamp(getattr(task, "expires", None))
    try:
        run_count = int(getattr(task, "total_run_count", 0) or 0)
    except (TypeError, ValueError):
        run_count = 0
    info["total_run_count"] = run_count

    try:
        schedule_obj = task.schedule
    except Exception:  # pragma: no cover - schedule property may raise
        schedule_obj = None

    if schedule_obj is not None:
        try:
            info["schedule"] = str(schedule_obj)
        except Exception:  # pragma: no cover - schedule string conversion failed
            info["schedule"] = ""

    info["next_run"] = _auto_upgrade_next_check()
    return info


def _suite_uptime() -> str:
    """Return a human-readable uptime for the running suite when possible."""

    try:
        import psutil
    except Exception:
        return ""

    try:
        boot_timestamp = float(psutil.boot_time())
    except Exception:
        return ""

    if not boot_timestamp:
        return ""

    boot_time = datetime.fromtimestamp(boot_timestamp, tz=timezone.utc)
    now = timezone.now()
    if boot_time > now:
        return ""

    return timesince(boot_time, now)


def _build_auto_upgrade_report(*, limit: int = AUTO_UPGRADE_LOG_LIMIT) -> dict[str, object]:
    """Assemble the composite auto-upgrade report for the admin view."""

    base_dir = Path(settings.BASE_DIR)
    mode_info = _read_auto_upgrade_mode(base_dir)
    log_info = _load_auto_upgrade_log_entries(base_dir, limit=limit)
    skip_revisions = _load_auto_upgrade_skip_revisions(base_dir)
    schedule_info = _load_auto_upgrade_schedule()

    mode_value = str(mode_info.get("mode", "version"))
    is_latest = mode_value.lower() == "latest"

    settings_info = {
        "enabled": bool(mode_info.get("enabled", False)),
        "mode": mode_value,
        "is_latest": is_latest,
        "lock_exists": bool(mode_info.get("lock_exists", False)),
        "read_error": bool(mode_info.get("read_error", False)),
        "mode_file": str(_auto_upgrade_mode_file(base_dir)),
        "skip_revisions": skip_revisions,
        "task_name": AUTO_UPGRADE_TASK_NAME,
        "task_path": AUTO_UPGRADE_TASK_PATH,
        "log_path": str(log_info.get("path")),
        "suite_uptime": _suite_uptime(),
    }

    return {
        "settings": settings_info,
        "schedule": schedule_info,
        "log_entries": log_info.get("entries", []),
        "log_error": str(log_info.get("error", "")),
    }


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

    upgrade_guard_active = bool(info.get("upgrade_guard_active", False))
    add_field(
        _("Upgrade restart guard active"),
        "UPGRADE_GUARD_ACTIVE",
        upgrade_guard_active,
        field_type="boolean",
    )
    add_field(
        _("Automatic restart deadline"),
        "UPGRADE_RESTART_DEADLINE",
        info.get("upgrade_restart_deadline", ""),
        visible=upgrade_guard_active and bool(info.get("upgrade_restart_deadline", "")),
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


def _configured_backend_port(base_dir: Path) -> int:
    lock_file = base_dir / "locks" / "backend_port.lck"
    try:
        raw = lock_file.read_text().strip()
    except OSError:
        return 8888
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 8888
    if 1 <= value <= 65535:
        return value
    return 8888


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


def _read_upgrade_guard_state(service: str) -> tuple[bool, str]:
    """Inspect the systemd timer responsible for post-upgrade recovery."""

    if not service or not shutil.which("systemctl"):
        return False, ""

    guard_timer = f"{service}-upgrade-guard.timer"
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                guard_timer,
                "--property=ActiveState",
                "--property=NextElapseUSecRealtime",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False, ""

    if result.returncode != 0:
        return False, ""

    active_state = ""
    next_elapse = ""
    for line in result.stdout.splitlines():
        if line.startswith("ActiveState="):
            active_state = line.partition("=")[2].strip()
        elif line.startswith("NextElapseUSecRealtime="):
            next_elapse = line.partition("=")[2].strip()

    is_active = active_state in {"active", "activating"}
    if not is_active or next_elapse == "0":
        next_elapse = ""

    return is_active, next_elapse


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

    upgrade_guard_active, upgrade_restart_deadline = _read_upgrade_guard_state(service)
    info["upgrade_guard_active"] = upgrade_guard_active
    info["upgrade_restart_deadline"] = upgrade_restart_deadline

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


def _system_upgrade_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Upgrade Report"),
            "auto_upgrade_report": _build_auto_upgrade_report(),
            "failover_status": read_failover_status(Path(settings.BASE_DIR)),
        }
    )
    return TemplateResponse(request, "admin/system_upgrade_report.html", context)


class PendingTodoForm(forms.ModelForm):
    mark_done = forms.BooleanField(required=False, label=_("Approve"))

    class Meta:
        model = Todo
        fields = [
            "request",
            "request_details",
            "url",
            "version",
            "generated_for_version",
            "generated_for_revision",
            "on_done_condition",
        ]
        widgets = {
            "request_details": forms.Textarea(attrs={"rows": 3}),
            "on_done_condition": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in [
            "request",
            "url",
            "version",
            "generated_for_version",
            "generated_for_revision",
        ]:
            self.fields[name].widget.attrs.setdefault("class", "vTextField")
        for name in ["request_details", "on_done_condition"]:
            self.fields[name].widget.attrs.setdefault("class", "vLargeTextField")

        mark_done_widget = self.fields["mark_done"].widget
        existing_classes = mark_done_widget.attrs.get("class", "").split()
        if "approve-checkbox" not in existing_classes:
            existing_classes.append("approve-checkbox")
        mark_done_widget.attrs["class"] = " ".join(
            class_name for class_name in existing_classes if class_name
        )


PendingTodoFormSet = modelformset_factory(Todo, form=PendingTodoForm, extra=0)


def _system_pending_todos_report_view(request):
    Todo.refresh_active()
    queryset = (
        Todo.objects.filter(
            is_deleted=False, done_on__isnull=True, stale_on__isnull=True
        ).order_by("request")
    )
    formset = PendingTodoFormSet(
        request.POST or None,
        queryset=queryset,
        prefix="todos",
    )

    if request.method == "POST":
        if formset.is_valid():
            approved_count = 0
            edited_count = 0
            for form in formset.forms:
                mark_done = form.cleaned_data.get("mark_done")
                todo = form.save(commit=False)
                has_changes = form.has_changed()
                if mark_done and todo.done_on is None:
                    todo.done_on = timezone.now()
                    todo.populate_done_metadata(request.user)
                    approved_count += 1
                    has_changes = True
                if has_changes:
                    todo.save()
                    if form.has_changed():
                        edited_count += 1
                if has_changes and form.has_changed():
                    form.save_m2m()

            if approved_count or edited_count:
                message_parts: list[str] = []
                if edited_count:
                    message_parts.append(
                        ngettext(
                            "%(count)d TODO updated.",
                            "%(count)d TODOs updated.",
                            edited_count,
                        )
                        % {"count": edited_count}
                    )
                if approved_count:
                    message_parts.append(
                        ngettext(
                            "%(count)d TODO approved.",
                            "%(count)d TODOs approved.",
                            approved_count,
                        )
                        % {"count": approved_count}
                    )
                messages.success(request, " ".join(message_parts))
            else:
                messages.info(
                    request,
                    _("No changes were applied to the pending TODOs."),
                )
            return HttpResponseRedirect(reverse("admin:system-pending-todos-report"))
        else:
            messages.error(request, _("Please correct the errors below."))

    rows = [
        {
            "form": form,
            "todo": form.instance,
        }
        for form in formset.forms
    ]

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Pending TODOs Report"),
            "formset": formset,
            "rows": rows,
        }
    )
    return TemplateResponse(
        request,
        "admin/system_pending_todos_report.html",
        context,
    )


def _system_changelog_report_view(request):
    """Render the changelog report with lazy-loaded sections."""

    try:
        initial_page = changelog.get_initial_page()
    except changelog.ChangelogError as exc:
        initial_sections = tuple()
        has_more = False
        next_page = None
        error_message = str(exc)
    else:
        initial_sections = initial_page.sections
        has_more = initial_page.has_more
        next_page = initial_page.next_page
        error_message = ""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Changelog Report"),
            "initial_sections": initial_sections,
            "has_more_sections": has_more,
            "next_page": next_page,
            "initial_section_count": len(initial_sections),
            "error_message": error_message,
            "loading_label": _("Loading more changes…"),
            "error_label": _("Unable to load additional changes."),
            "complete_label": _("You're all caught up."),
        }
    )
    return TemplateResponse(request, "admin/system_changelog_report.html", context)


def _system_changelog_report_data_view(request):
    """Return additional changelog sections for infinite scrolling."""

    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)

    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        return JsonResponse({"error": _("Invalid offset.")}, status=400)

    try:
        page_data = changelog.get_page(page_number, per_page=1, offset=offset)
    except changelog.ChangelogError as exc:
        return JsonResponse({"error": str(exc)}, status=503)

    if not page_data.sections:
        return JsonResponse({"html": "", "has_more": False, "next_page": None})

    html = render_to_string(
        "includes/changelog/section_list.html",
        {"sections": page_data.sections, "variant": "admin"},
        request=request,
    )
    return JsonResponse(
        {"html": html, "has_more": page_data.has_more, "next_page": page_data.next_page}
    )


def _trigger_upgrade_check(*, channel_override: str | None = None) -> bool:
    """Return ``True`` when the upgrade check was queued asynchronously."""

    try:
        if channel_override:
            check_github_updates.delay(channel_override=channel_override)
        else:
            check_github_updates.delay()
    except Exception:
        logger.exception("Failed to enqueue upgrade check; running synchronously instead")
        if channel_override:
            check_github_updates(channel_override=channel_override)
        else:
            check_github_updates()
        return False
    return True


def _upgrade_redirect(request, fallback: str) -> HttpResponseRedirect:
    """Return a safe redirect response for upgrade-related form submissions."""

    candidate = (request.POST.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return HttpResponseRedirect(candidate)
    return HttpResponseRedirect(fallback)


def _system_trigger_upgrade_check_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    requested_channel = (request.POST.get("channel") or "normal").lower()
    channel_choice = UPGRADE_CHANNEL_CHOICES.get(
        requested_channel, UPGRADE_CHANNEL_CHOICES["normal"]
    )
    override_value = channel_choice.get("override")
    channel_override = override_value if isinstance(override_value, str) else None
    channel_label = None
    if channel_override:
        channel_label = str(channel_choice["label"])

    base_dir = Path(settings.BASE_DIR)
    _clear_auto_upgrade_skip_revisions(base_dir)

    try:
        queued = _trigger_upgrade_check(channel_override=channel_override)
    except Exception as exc:  # pragma: no cover - unexpected failure
        logger.exception("Unable to trigger upgrade check")
        messages.error(
            request,
            _("Unable to trigger an upgrade check: %(error)s")
            % {"error": str(exc)},
        )
    else:
        detail_message = ""
        if channel_label:
            detail_message = _(
                "It will run using the %(channel)s channel for this execution without changing the configured mode."
            ) % {"channel": channel_label}
        if queued:
            base_message = _("Upgrade check requested. The task will run shortly.")
        else:
            base_message = _(
                "Upgrade check started locally. Review the auto-upgrade log for progress."
            )
        if detail_message:
            messages.success(
                request,
                format_html("{} {}", base_message, detail_message),
            )
        else:
            messages.success(request, base_message)

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def _system_clear_failover_lock_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:index"))

    base_dir = Path(settings.BASE_DIR)
    clear_failover_lock(base_dir)
    messages.success(
        request,
        _("Failover alert dismissed. Auto-upgrade retries remain available."),
    )
    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
            path(
                "system/changelog/",
                admin.site.admin_view(_system_changelog_report_view),
                name="system-changelog-report",
            ),
            path(
                "system/changelog/data/",
                admin.site.admin_view(_system_changelog_report_data_view),
                name="system-changelog-data",
            ),
            path(
                "system/pending-todos-report/",
                admin.site.admin_view(_system_pending_todos_report_view),
                name="system-pending-todos-report",
            ),
            path(
                "system/upgrade-report/",
                admin.site.admin_view(_system_upgrade_report_view),
                name="system-upgrade-report",
            ),
            path(
                "system/upgrade-report/run-check/",
                admin.site.admin_view(_system_trigger_upgrade_check_view),
                name="system-upgrade-run-check",
            ),
            path(
                "system/upgrade-report/dismiss-failover/",
                admin.site.admin_view(_system_clear_failover_lock_view),
                name="system-upgrade-dismiss-failover",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls

