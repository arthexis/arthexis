from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from functools import lru_cache
from pathlib import Path
import json
import os
import re
import socket
import subprocess
import shutil
import logging
from typing import Iterable
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import admin, messages
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

from config.request_utils import is_https_request
from apps.celery.utils import enqueue_task, is_celery_enabled
from apps.core.auto_upgrade import (
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES,
    auto_upgrade_fast_lane_enabled,
    auto_upgrade_fast_lane_lock_file,
    auto_upgrade_base_dir,
    auto_upgrade_log_file,
    set_auto_upgrade_fast_lane,
    ensure_auto_upgrade_periodic_task,
)
from apps.release.release import (
    _git_authentication_missing,
    _git_remote_url,
    _manager_git_credentials,
    _remote_with_credentials,
)
from apps.core.tasks import check_github_updates, _read_auto_upgrade_failure_count
from apps.core.uptime_constants import SUITE_UPTIME_LOCK_MAX_AGE, SUITE_UPTIME_LOCK_NAME
from apps.nginx.renderers import generate_primary_config
from apps.screens.startup_notifications import lcd_feature_enabled
from apps.cards.rfid_service import rfid_service_enabled
from apps.core.systemctl import _systemctl_command
from utils import revision
from apps.core import changelog


AUTO_UPGRADE_LOCK_NAME = "auto_upgrade.lck"
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
AUTO_UPGRADE_LOG_LIMIT = 30
UPGRADE_REVISION_SESSION_KEY = "system_upgrade_revision_info"

STARTUP_REPORT_LOG_NAME = "startup-report.log"
STARTUP_REPORT_DEFAULT_LIMIT = 50
STARTUP_CLOCK_DRIFT_THRESHOLD = timedelta(minutes=5)

REVISION_STATUS_CURRENT = "current"
REVISION_STATUS_OUTDATED = "outdated"
REVISION_STATUS_ERROR = "error"
REVISION_STATUS_UNKNOWN = "unknown"
REVISION_STATE_OK = "ok"
REVISION_STATE_WARNING = "warning"
REVISION_STATE_ERROR = "error"


UPGRADE_CHANNEL_CHOICES: dict[str, dict[str, object]] = {
    "stable": {"override": "stable", "label": _("Stable")},
    "unstable": {"override": "unstable", "label": _("Unstable")},
    # Legacy aliases
    "normal": {"override": "stable", "label": _("Stable")},
    "latest": {"override": "latest", "label": _("Latest")},
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
    return base_dir / ".locks" / AUTO_UPGRADE_LOCK_NAME


def _auto_upgrade_skip_file(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_SKIP_LOCK_NAME


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


def _resolve_auto_upgrade_now(schedule) -> datetime:
    """Return the current time with defensive fallbacks."""

    try:
        return schedule.maybe_make_aware(schedule.now())
    except Exception:
        try:
            return timezone.localtime()
        except Exception:
            return timezone.now()


def _normalize_auto_upgrade_time(
    raw_value: datetime | None, schedule
) -> datetime | None:
    """Return *raw_value* normalized to an aware datetime when possible."""

    if raw_value is None:
        return None

    try:
        return schedule.maybe_make_aware(raw_value)
    except Exception:
        try:
            if timezone.is_naive(raw_value):
                return timezone.make_aware(raw_value, timezone.get_current_timezone())
        except Exception:
            return raw_value
        return raw_value


def _resolve_auto_upgrade_reference_time(
    last_run_at: datetime | None, schedule, default: datetime
) -> datetime:
    """Return the reference datetime for remaining schedule estimates."""

    reference = _normalize_auto_upgrade_time(last_run_at, schedule)
    return reference if reference is not None else default


def _build_next_run_timestamp(schedule, reference: datetime, now: datetime) -> str:
    """Return the formatted next-run timestamp for *schedule*."""

    try:
        remaining = schedule.remaining_estimate(reference)
    except Exception:
        return ""

    next_run = now + remaining
    return _format_timestamp(next_run)


def _format_next_run_from_reference(
    reference: datetime | None, *, interval_minutes: int
) -> str:
    """Return a formatted next-run time using a known interval."""

    if reference is None:
        return ""

    normalized = reference
    try:
        if timezone.is_naive(normalized):
            normalized = timezone.make_aware(
                normalized, timezone.get_current_timezone()
            )
    except Exception as exc:
        logger.warning(
            "Failed to make timestamp aware in _format_next_run_from_reference: %s",
            exc,
        )
        normalized = reference

    next_run = normalized + timedelta(minutes=interval_minutes)
    return _format_timestamp(next_run)


def _predict_auto_upgrade_next_run(task) -> str:
    """Return a display-ready next-run timestamp for *task*."""

    if not task:
        return ""

    if not getattr(task, "enabled", False):
        return str(_("Disabled"))

    try:
        schedule = task.schedule
    except Exception:
        return ""

    if schedule is None:
        return ""

    now = _resolve_auto_upgrade_now(schedule)

    start_time = getattr(task, "start_time", None)
    if start_time is not None:
        candidate_start = _normalize_auto_upgrade_time(start_time, schedule)
        if candidate_start and candidate_start > now:
            return _format_timestamp(candidate_start)

    last_run_at = getattr(task, "last_run_at", None)
    reference = _resolve_auto_upgrade_reference_time(last_run_at, schedule, now)

    return _build_next_run_timestamp(schedule, reference, now)


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

    return _predict_auto_upgrade_next_run(task)


def _read_auto_upgrade_mode(base_dir: Path) -> dict[str, object]:
    """Return metadata describing the configured auto-upgrade mode."""

    mode_file = _auto_upgrade_mode_file(base_dir)
    info: dict[str, object] = {
        "mode": "stable",
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

    mode = raw_value or "stable"
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


def _format_revision_error(prefix: str, exc: Exception) -> str:
    """Return a human-readable revision error string for display."""

    detail = ""
    if isinstance(exc, subprocess.CalledProcessError):
        detail = (exc.stderr or exc.stdout or "").strip()
        if not detail:
            detail = str(exc)
    else:
        detail = str(exc)

    if not detail:
        return prefix
    return f"{prefix}: {detail}"


def _load_upgrade_revision_info(base_dir: Path, branch: str = "main") -> dict[str, str]:
    """Return the current local and origin revisions for comparison."""

    local_revision = ""
    origin_revision = ""
    origin_revision_error = ""

    try:
        local_revision = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=base_dir,
                stderr=subprocess.STDOUT,
                text=True,
            )
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        local_revision = ""

    try:
        remote_url_proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        origin_revision_error = _format_revision_error(
            str(_("Origin revision unavailable")), exc
        )
        return {
            "local_revision": local_revision,
            "origin_revision": origin_revision,
            "origin_revision_error": origin_revision_error,
        }

    if remote_url_proc.returncode != 0 or not (remote_url_proc.stdout or "").strip():
        origin_revision_error = str(_("Origin remote is not configured."))
        return {
            "local_revision": local_revision,
            "origin_revision": origin_revision,
            "origin_revision_error": origin_revision_error,
        }

    try:
        subprocess.run(
            ["git", "fetch", "origin", branch],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        origin_revision_error = _format_revision_error(
            str(_("Unable to refresh origin revision")), exc
        )
        return {
            "local_revision": local_revision,
            "origin_revision": origin_revision,
            "origin_revision_error": origin_revision_error,
        }

    try:
        origin_revision = (
            subprocess.check_output(
                ["git", "rev-parse", f"origin/{branch}"],
                cwd=base_dir,
                stderr=subprocess.STDOUT,
                text=True,
            )
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        origin_revision_error = _format_revision_error(
            str(_("Unable to read origin revision")), exc
        )

    return {
        "local_revision": local_revision,
        "origin_revision": origin_revision,
        "origin_revision_error": origin_revision_error,
    }


def _prepare_revision_info(
    revision_info: dict[str, object] | None,
) -> dict[str, object]:
    """Return revision metadata with optional last-checked timestamp."""

    details: dict[str, object] = {
        "local_revision": "",
        "origin_revision": "",
        "origin_revision_error": "",
        "revision_checked_at": None,
        "revision_checked_label": "",
        "revision_status": "",
        "revision_status_label": "",
        "revision_status_state": "",
        "ci_status": "",
    }

    if not revision_info:
        return details

    details.update({
        "local_revision": str(revision_info.get("local_revision", "")),
        "origin_revision": str(revision_info.get("origin_revision", "")),
        "origin_revision_error": str(
            revision_info.get("origin_revision_error", "")
        ),
        "ci_status": str(revision_info.get("ci_status", "")),
    })

    checked_value = revision_info.get("revision_checked_at") or revision_info.get(
        "checked_at"
    )
    parsed_checked_at: datetime | None = None
    if isinstance(checked_value, datetime):
        parsed_checked_at = checked_value
    elif checked_value:
        parsed_checked_at = _parse_log_timestamp(str(checked_value))

    if parsed_checked_at:
        details["revision_checked_at"] = parsed_checked_at
        details["revision_checked_label"] = _format_timestamp(parsed_checked_at)
    elif checked_value:
        details["revision_checked_label"] = str(checked_value)

    local_revision = details["local_revision"]
    origin_revision = details["origin_revision"]
    origin_revision_error = details["origin_revision_error"]

    if local_revision and origin_revision:
        if local_revision == origin_revision:
            details["revision_status"] = REVISION_STATUS_CURRENT
            details["revision_status_label"] = str(_("Up to date"))
            details["revision_status_state"] = REVISION_STATE_OK
        else:
            details["revision_status"] = REVISION_STATUS_OUTDATED
            details["revision_status_label"] = str(_("Update available"))
            details["revision_status_state"] = REVISION_STATE_WARNING
    elif origin_revision_error:
        details["revision_status"] = REVISION_STATUS_ERROR
        details["revision_status_label"] = str(_("Revision check failed"))
        details["revision_status_state"] = REVISION_STATE_ERROR
    else:
        details["revision_status"] = REVISION_STATUS_UNKNOWN
        details["revision_status_label"] = str(_("Revision status unavailable"))
        details["revision_status_state"] = REVISION_STATE_WARNING

    return details


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

    log_file = auto_upgrade_log_file(base_dir)
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
        entries.append(
            {
                "timestamp": timestamp_display,
                "timestamp_raw": timestamp,
                "message": message,
            }
        )

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
    base_dir = Path(settings.BASE_DIR)
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
        "failure_count": _read_auto_upgrade_failure_count(base_dir),
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
    try:
        failure_count = int(info.get("failure_count", 0) or 0)
    except (TypeError, ValueError):
        failure_count = 0
    info["failure_count"] = failure_count
    info["total_run_count"] = 0 if failure_count else run_count

    try:
        schedule_obj = task.schedule
    except Exception:  # pragma: no cover - schedule property may raise
        schedule_obj = None

    if schedule_obj is not None:
        try:
            info["schedule"] = str(schedule_obj)
        except Exception:  # pragma: no cover - schedule string conversion failed
            info["schedule"] = ""

    info["next_run"] = _predict_auto_upgrade_next_run(task)
    return info


def _suite_uptime_lock_path(base_dir: Path | str | None = None) -> Path:
    """Return the lockfile path used to store suite uptime metadata."""

    root = Path(base_dir) if base_dir is not None else Path(settings.BASE_DIR)
    return root / ".locks" / SUITE_UPTIME_LOCK_NAME


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


def _suite_uptime_lock_info(*, now: datetime | None = None) -> dict[str, object]:
    """Return parsed metadata for the suite uptime lock file."""

    current_time = now or timezone.now()
    lock_path = _suite_uptime_lock_path()
    info: dict[str, object] = {
        "path": lock_path,
        "exists": False,
        "started_at": None,
        "fresh": False,
    }

    try:
        stats = lock_path.stat()
    except OSError:
        return info

    info["exists"] = True
    try:
        raw_payload = lock_path.read_text(encoding="utf-8")
    except OSError:
        raw_payload = ""

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}

    started_at = _parse_suite_uptime_timestamp(
        payload.get("started_at") or payload.get("boot_time")
    )
    info["started_at"] = started_at
    info["fresh"] = bool(
        started_at
        and started_at <= current_time
        and _suite_uptime_lock_is_fresh(lock_path, current_time)
    )

    return info


def _parse_suite_uptime_timestamp(value: object) -> datetime | None:
    """Parse an ISO timestamp from the suite uptime lock file."""

    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text[-1] in {"Z", "z"}:
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if timezone.is_naive(parsed):
        try:
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        except Exception:
            return None

    return parsed


def _suite_uptime_lock_is_fresh(lock_path: Path, now: datetime) -> bool:
    """Return ``True`` when the lockfile heartbeat is within the freshness window."""

    try:
        stats = lock_path.stat()
    except OSError:
        return False

    heartbeat = datetime.fromtimestamp(stats.st_mtime, tz=datetime_timezone.utc)
    now_utc = now.astimezone(datetime_timezone.utc)
    if heartbeat > now_utc:
        return False
    return (now_utc - heartbeat) <= SUITE_UPTIME_LOCK_MAX_AGE


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
        return {"available": False}

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


_DAY_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


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


def _format_datetime(dt: datetime | None) -> str:
    if not dt:
        return ""
    return date_format(timezone.localtime(dt), "Y-m-d H:i")


def _startup_report_log_path(base_dir: Path | None = None) -> Path:
    root = Path(settings.BASE_DIR) if base_dir is None else Path(base_dir)
    return root / "logs" / STARTUP_REPORT_LOG_NAME


def _startup_report_reference_time(log_path: Path) -> datetime | None:
    """Return the log's modification time in the current timezone."""

    try:
        mtime = log_path.stat().st_mtime
    except OSError:
        return None

    try:
        return timezone.make_aware(datetime.fromtimestamp(mtime))
    except (OverflowError, ValueError, OSError):
        return None


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


def _build_auto_upgrade_report(
    *, limit: int = AUTO_UPGRADE_LOG_LIMIT, revision_info: dict[str, object] | None = None
) -> dict[str, object]:
    """Assemble the composite auto-upgrade report for the admin view."""

    base_dir = auto_upgrade_base_dir()
    fast_lane_enabled = auto_upgrade_fast_lane_enabled(base_dir)
    mode_info = _read_auto_upgrade_mode(base_dir)
    log_info = _load_auto_upgrade_log_entries(base_dir, limit=limit)
    skip_revisions = _load_auto_upgrade_skip_revisions(base_dir)
    schedule_info = _load_auto_upgrade_schedule()
    schedule_info["fast_lane_enabled"] = fast_lane_enabled

    # ``last_run_at`` may be empty when Celery Beat has not executed the
    # periodic task yet or when inline task execution bypasses the scheduler,
    # so fall back to the most recent log entry for display purposes.
    used_log_last_run = False
    entries = log_info.get("entries") or []
    last_log_entry = next(iter(entries), None)
    last_log_timestamp_raw = None
    if last_log_entry:
        last_log_timestamp_raw = last_log_entry.get("timestamp_raw")
    if not schedule_info.get("last_run_at") and last_log_entry:
        if last_log_entry.get("timestamp"):
            schedule_info["last_run_at"] = last_log_entry["timestamp"]
            used_log_last_run = True

    if fast_lane_enabled and not schedule_info.get("description"):
        schedule_info["description"] = _(
            "Fast Lane enabled: upgrade checks run hourly."
        )
    schedule_disabled = schedule_info.get("enabled") is False
    if schedule_info.get("next_run") == str(_("Disabled")):
        schedule_disabled = True

    if (
        fast_lane_enabled
        and used_log_last_run
        and last_log_timestamp_raw is not None
        and not schedule_disabled
    ):
        schedule_info["next_run"] = _format_next_run_from_reference(
            last_log_timestamp_raw,
            interval_minutes=AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES,
        )

    raw_mode_value = str(mode_info.get("mode", "stable"))
    normalized_mode = raw_mode_value.lower() or "stable"
    resolved_mode = {
        "version": "stable",
        "normal": "stable",
        "regular": "stable",
    }.get(normalized_mode, normalized_mode)
    if not resolved_mode:
        resolved_mode = "stable"
    is_unstable_mode = resolved_mode in {"unstable", "latest"}
    is_stable_mode = resolved_mode == "stable"
    is_latest_mode = normalized_mode == "latest"

    revision_details = _prepare_revision_info(revision_info)

    suite_details = _suite_uptime_details()
    suite_boot_time = suite_details.get("boot_time")
    suite_lock_started_at = suite_details.get("lock_started_at")

    settings_info = {
        "enabled": bool(mode_info.get("enabled", False)),
        "mode": resolved_mode,
        "raw_mode": raw_mode_value,
        "channel": resolved_mode,
        "is_unstable_mode": is_unstable_mode,
        "is_stable_mode": is_stable_mode,
        "is_latest": is_latest_mode,
        "lock_exists": bool(mode_info.get("lock_exists", False)),
        "read_error": bool(mode_info.get("read_error", False)),
        "mode_file": str(_auto_upgrade_mode_file(base_dir)),
        "fast_lane_enabled": fast_lane_enabled,
        "fast_lane_lock": str(auto_upgrade_fast_lane_lock_file(base_dir)),
        "skip_revisions": skip_revisions,
        "task_name": AUTO_UPGRADE_TASK_NAME,
        "task_path": AUTO_UPGRADE_TASK_PATH,
        "log_path": str(log_info.get("path")),
        "suite_uptime": str(suite_details.get("uptime", "")),
        "suite_uptime_details": {
            "available": bool(suite_details.get("available") or suite_details.get("uptime")),
            "boot_time_label": suite_details.get("boot_time_label", ""),
            "lock_started_at_label": _format_datetime(suite_lock_started_at)
            if isinstance(suite_lock_started_at, datetime)
            else "",
            "lock_predates_boot": bool(
                isinstance(suite_lock_started_at, datetime)
                and isinstance(suite_boot_time, datetime)
                and suite_lock_started_at < suite_boot_time
            ),
        },
    }
    settings_info.update(revision_details)

    log_entries = log_info.get("entries", [])
    last_log_entry = log_entries[0] if log_entries else {}

    issues: list[dict[str, str]] = []
    status_state = "ok"

    def note(label: str, *, severity: str = "warning") -> None:
        nonlocal status_state
        issues.append({"label": label, "severity": severity})
        if severity == "error":
            status_state = "error"
        elif status_state != "error":
            status_state = severity

    if log_info.get("error"):
        note(str(log_info["error"]), severity="error")

    if settings_info.get("read_error"):
        note(
            str(
                _("The auto-upgrade mode could not be read; verify the lock file permissions.")),
            severity="error",
        )

    if not settings_info.get("enabled"):
        note(str(_("Auto-upgrades are currently disabled.")), severity="warning")

    if schedule_info.get("available"):
        if not schedule_info.get("configured"):
            note(str(_("The auto-upgrade periodic task has not been created yet.")), severity="warning")
        elif not schedule_info.get("enabled"):
            note(str(_("The periodic task is present but disabled.")), severity="warning")
    else:
        if schedule_info.get("error"):
            note(str(schedule_info["error"]), severity="error")
        else:
            note(str(_("Scheduling information is unavailable.")), severity="warning")

    failure_count = schedule_info.get("failure_count", 0) or 0
    try:
        failure_count = int(failure_count)
    except (TypeError, ValueError):
        failure_count = 0
    if failure_count:
        note(
            str(
                ngettext(
                    "There is %(count)s recorded upgrade failure.",
                    "There are %(count)s recorded upgrade failures.",
                    failure_count,
                )
                % {"count": failure_count}
            ),
            severity="warning",
        )

    headline = _("Auto-upgrade status looks good.")
    if status_state == "warning":
        headline = _("Auto-upgrade needs attention.")
    elif status_state == "error":
        headline = _("Auto-upgrade is blocked or misconfigured.")

    summary = {
        "state": status_state,
        "headline": headline,
        "last_activity": {
            "timestamp": last_log_entry.get("timestamp", ""),
            "message": last_log_entry.get("message", ""),
        },
        "next_run": schedule_info.get("next_run", ""),
        "issues": issues,
    }

    return {
        "settings": settings_info,
        "schedule": schedule_info,
        "log_entries": log_entries,
        "log_error": str(log_info.get("error", "")),
        "summary": summary,
    }


def _resolve_auto_upgrade_namespace(key: str) -> str | None:
    """Resolve sigils within the legacy ``AUTO-UPGRADE`` namespace."""

    normalized = key.replace("-", "_").upper()
    if normalized == "NEXT_CHECK":
        return _auto_upgrade_next_check()
    return None


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
    lock_file = base_dir / ".locks" / "backend_port.lck"
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


def _normalize_nginx_content(content: str) -> str:
    """Return *content* with trailing newlines removed for comparison."""

    return content.rstrip("\n")


def _resolve_nginx_mode(base_dir: Path) -> str:
    """Return the configured nginx mode with a safe fallback."""

    mode_file = base_dir / ".locks" / "nginx_mode.lck"
    try:
        raw_mode = mode_file.read_text().strip()
    except OSError:
        return "internal"

    normalized = raw_mode.lower() or "internal"
    if normalized not in {"internal", "public"}:
        return "internal"
    return normalized


def _nginx_site_path() -> Path:
    configured_path = getattr(settings, "NGINX_SITE_PATH", None) or ""
    if configured_path:
        return Path(configured_path)
    return Path("/etc/nginx/sites-enabled/arthexis.conf")


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


def _gather_info() -> dict:
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

    # Use settings.NODE_ROLE as the single source of truth for the node role.
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
    info["auto_upgrade_next_check"] = _auto_upgrade_next_check()

    return info


def _configured_service_units(base_dir: Path) -> list[dict[str, str]]:
    """Return service units configured for this instance."""

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

    service_units: list[dict[str, str]] = []
    seen_units: set[str] = set()

    def _add_unit(unit_name: str, *, label: str | None = None) -> None:
        normalized = unit_name.strip()
        if not normalized or normalized in seen_units:
            return

        seen_units.add(normalized)
        unit_display = normalized
        unit = normalized
        if normalized.endswith(".service"):
            unit = normalized.removesuffix(".service")
        else:
            unit_display = f"{normalized}.service"

        service_units.append(
            {
                "label": label or normalized,
                "unit": unit,
                "unit_display": unit_display,
            }
        )

    for unit_name in systemd_units:
        label = None
        if service_name:
            base_label_map = {
                f"{service_name}.service": str(_("Suite service")),
                f"celery-{service_name}.service": str(_("Celery worker")),
                f"celery-beat-{service_name}.service": str(_("Celery beat")),
                f"lcd-{service_name}.service": str(_("LCD screen")),
                f"rfid-{service_name}.service": str(_("RFID scanner service")),
            }
            label = base_label_map.get(unit_name.strip())

        _add_unit(unit_name, label=label)

    if service_units:
        return service_units

    if not service_name:
        return []

    _add_unit(service_name, label=str(_("Suite service")))

    if is_celery_enabled(lock_dir / "celery.lck"):
        for prefix, label in [
            ("celery", _("Celery worker")),
            ("celery-beat", _("Celery beat")),
        ]:
            _add_unit(f"{prefix}-{service_name}", label=str(label))

    if lcd_feature_enabled(lock_dir):
        _add_unit(f"lcd-{service_name}", label=str(_("LCD screen")))
    if rfid_service_enabled(lock_dir):
        _add_unit(
            f"rfid-{service_name}",
            label=str(_("RFID scanner service")),
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


def _build_services_report() -> dict[str, object]:
    base_dir = Path(settings.BASE_DIR)
    configured_units = _configured_service_units(base_dir)
    command = _systemctl_command()

    services: list[dict[str, object]] = []
    for unit in configured_units:
        status_info = _systemd_unit_status(unit["unit"], command=command)
        services.append({**unit, **status_info})

    return {
        "services": services,
        "systemd_available": bool(command),
        "has_services": bool(configured_units),
    }




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


def _system_startup_report_view(request):
    try:
        limit = int(request.GET.get("limit", STARTUP_REPORT_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = STARTUP_REPORT_DEFAULT_LIMIT

    if limit < 1:
        limit = STARTUP_REPORT_DEFAULT_LIMIT

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Startup Report"),
            "startup_report": _read_startup_report(limit=limit),
            "startup_report_limit": limit,
            "startup_report_options": (10, 25, 50, 100, 200),
        }
    )
    return TemplateResponse(request, "admin/system_startup_report.html", context)


def _system_uptime_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Uptime Report"),
            "uptime_report": _build_uptime_report(),
        }
    )
    return TemplateResponse(request, "admin/system_uptime_report.html", context)


def _system_services_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Suite Services Report"),
            "services_report": _build_services_report(),
        }
    )
    return TemplateResponse(request, "admin/system_services_report.html", context)


def _system_nginx_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("NGINX Report"),
            "nginx_report": _build_nginx_report(),
        }
    )
    return TemplateResponse(request, "admin/system_nginx_report.html", context)


def _system_upgrade_report_view(request):
    revision_info = None
    session = getattr(request, "session", None)
    if session is not None:
        revision_info = session.pop(UPGRADE_REVISION_SESSION_KEY, None)
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Upgrade Report"),
            "auto_upgrade_report": _build_auto_upgrade_report(
                **({"revision_info": revision_info} if revision_info is not None else {})
            ),
        }
    )
    return TemplateResponse(request, "admin/system_upgrade_report.html", context)


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

    def _run_sync_upgrade_check(channel_override: str | None = None) -> None:
        """Run the upgrade check synchronously with optional channel override."""

        if channel_override:
            check_github_updates(channel_override=channel_override)
        else:
            check_github_updates()

    broker_url = str(getattr(settings, "CELERY_BROKER_URL", "")).strip()
    if not broker_url or broker_url.startswith("memory://"):
        _run_sync_upgrade_check(channel_override)
        return False

    if not is_celery_enabled():
        _run_sync_upgrade_check(channel_override)
        return False

    if channel_override:
        queued = enqueue_task(
            check_github_updates, channel_override=channel_override, require_enabled=False
        )
    else:
        queued = enqueue_task(check_github_updates, require_enabled=False)

    if not queued:
        logger.warning(
            "Failed to enqueue upgrade check; running synchronously instead"
        )
        _run_sync_upgrade_check(channel_override)
        return False
    return True


def _upgrade_redirect(request, fallback: str) -> HttpResponseRedirect:
    """Return a safe redirect response for upgrade-related form submissions."""

    candidate = (request.POST.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=is_https_request(request),
    ):
        return HttpResponseRedirect(candidate)
    return HttpResponseRedirect(fallback)


def _system_trigger_upgrade_check_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    requested_channel = (request.POST.get("channel") or "stable").lower()
    channel_choice = UPGRADE_CHANNEL_CHOICES.get(
        requested_channel, UPGRADE_CHANNEL_CHOICES["stable"]
    )
    override_value = channel_choice.get("override")
    channel_override = override_value if isinstance(override_value, str) else None
    channel_label = None
    if requested_channel == "stable":
        channel_override = None
    elif channel_override:
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


def _system_upgrade_revision_check_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    base_dir = Path(settings.BASE_DIR)
    revision_info = _load_upgrade_revision_info(base_dir)
    revision_info["revision_checked_at"] = timezone.now().isoformat()

    origin_revision = str(revision_info.get("origin_revision", ""))
    ci_status = ""
    if origin_revision:
        try:
            # CI status is retrieved on demand to avoid unnecessary API calls.
            from apps.core.tasks import _ci_status_for_revision

            ci_status = _ci_status_for_revision(base_dir, origin_revision) or ""
        except Exception:  # pragma: no cover - unexpected failure path
            logger.exception("Unable to fetch CI status for revision %s", origin_revision)
            ci_status = ""

    revision_info["ci_status"] = ci_status

    if hasattr(request, "session"):
        request.session[UPGRADE_REVISION_SESSION_KEY] = revision_info

    messages.success(request, _("Pre-upgrade checks refreshed."))

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def _system_toggle_fast_lane_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    action = (request.POST.get("fast_lane_action") or "").strip().lower()
    enable = action == "enable"

    base_dir = auto_upgrade_base_dir()
    updated = set_auto_upgrade_fast_lane(enable, base_dir=base_dir)

    if updated:
        ensure_auto_upgrade_periodic_task(base_dir=base_dir)
        if enable:
            messages.success(
                request,
                _(
                    "Fast Lane enabled. Upgrade checks will run once per hour until disabled."
                ),
            )
        else:
            messages.success(
                request,
                _(
                    "Fast Lane disabled. Upgrade checks will run on the configured channel cadence."
                ),
            )
    else:
        messages.error(request, _("Unable to update Fast Lane mode."))

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))

def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
            path(
                "system/startup-report/",
                admin.site.admin_view(_system_startup_report_view),
                name="system-startup-report",
            ),
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
                "system/uptime-report/",
                admin.site.admin_view(_system_uptime_report_view),
                name="system-uptime-report",
            ),
            path(
                "system/nginx-report/",
                admin.site.admin_view(_system_nginx_report_view),
                name="system-nginx-report",
            ),
            path(
                "system/services-report/",
                admin.site.admin_view(_system_services_report_view),
                name="system-services-report",
            ),
            path(
                "system/upgrade-report/",
                admin.site.admin_view(_system_upgrade_report_view),
                name="system-upgrade-report",
            ),
            path(
                "system/upgrade-report/check-revision/",
                admin.site.admin_view(_system_upgrade_revision_check_view),
                name="system-upgrade-check-revision",
            ),
            path(
                "system/upgrade-report/run-check/",
                admin.site.admin_view(_system_trigger_upgrade_check_view),
                name="system-upgrade-run-check",
            ),
            path(
                "system/upgrade-report/toggle-fast-lane/",
                admin.site.admin_view(_system_toggle_fast_lane_view),
                name="system-upgrade-toggle-fast-lane",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
