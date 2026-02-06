from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
import logging
import subprocess

from django.conf import settings
from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from apps.celery.utils import enqueue_task, is_celery_enabled
from apps.core.auto_upgrade import (
    AUTO_UPGRADE_TASK_NAME,
    AUTO_UPGRADE_TASK_PATH,
    auto_upgrade_failure_guide,
    auto_upgrade_base_dir,
    auto_upgrade_log_file,
    ensure_auto_upgrade_periodic_task,
)
from apps.core.tasks.auto_upgrade import (
    _read_auto_upgrade_failure_count,
    check_github_updates,
)

from .filesystem import _auto_upgrade_skip_file
from .ui import _format_datetime, _format_timestamp, _suite_uptime_details


AUTO_UPGRADE_LOG_LIMIT = 30
AUTO_UPGRADE_RECENT_ACTIVITY_HOURS = 48
UPGRADE_REVISION_SESSION_KEY = "system_upgrade_revision_info"

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


def _format_interval_minutes(interval_minutes: int) -> str:
    """Return a readable interval label for policy cadence."""
    if interval_minutes <= 0:
        return ""
    if interval_minutes % 1440 == 0:
        days = interval_minutes // 1440
        return str(ngettext("Every %(count)s day", "Every %(count)s days", days) % {"count": days})
    if interval_minutes % 60 == 0:
        hours = interval_minutes // 60
        return str(ngettext("Every %(count)s hour", "Every %(count)s hours", hours) % {"count": hours})
    return str(
        ngettext(
            "Every %(count)s minute",
            "Every %(count)s minutes",
            interval_minutes,
        )
        % {"count": interval_minutes}
    )


def _load_upgrade_policy_report() -> dict[str, object]:
    """Return policy metadata for the local node."""
    try:  # pragma: no cover - optional dependency
        from apps.nodes.models import Node, NodeUpgradePolicyAssignment
    except Exception:
        return {"policies": [], "manual": True, "error": str(_("Upgrade policy data unavailable."))}

    try:
        local = Node.get_local()
    except DatabaseError:
        return {"policies": [], "manual": True, "error": str(_("Upgrade policy data unavailable."))}

    if not local:
        return {"policies": [], "manual": True, "error": ""}

    try:
        assignments = (
            NodeUpgradePolicyAssignment.objects.select_related("policy")
            .filter(node=local)
            .order_by("policy__name")
        )
    except DatabaseError:
        return {"policies": [], "manual": True, "error": str(_("Upgrade policy data unavailable."))}

    policies: list[dict[str, object]] = []
    channels: set[str] = set()
    for assignment in assignments:
        policy = assignment.policy
        if not policy:
            continue
        channel = (policy.channel or "stable").lower()
        channel_label = str(getattr(policy, "get_channel_display", lambda: policy.channel)())
        channel_state = "ok" if channel == "stable" else "warning"
        channels.add(channel)
        policies.append(
            {
                "name": policy.name,
                "channel": channel,
                "channel_label": channel_label,
                "channel_state": channel_state,
                "interval_minutes": policy.interval_minutes,
                "interval_label": _format_interval_minutes(policy.interval_minutes),
                "requires_canaries": policy.requires_canaries,
                "requires_pypi": policy.requires_pypi_packages,
                "last_checked_at": assignment.last_checked_at,
                "last_checked_label": _format_timestamp(assignment.last_checked_at),
                "last_status": assignment.last_status,
            }
        )

    normalized_channels = {
        "unstable" if channel in {"unstable", "latest"} else "stable"
        for channel in channels
    }
    return {
        "policies": policies,
        "manual": not policies,
        "channels": sorted(normalized_channels),
        "stable_only": normalized_channels == {"stable"} if policies else False,
        "unstable_only": normalized_channels == {"unstable"} if policies else False,
        "error": "",
    }


def _read_auto_upgrade_mode(base_dir: Path) -> dict[str, object]:
    """Return metadata describing the configured upgrade policy state."""

    del base_dir
    policy_info = _load_upgrade_policy_report()
    channels = policy_info.get("channels") or []
    mode = "manual"
    if len(channels) == 1:
        mode = channels[0]
    elif channels:
        mode = "mixed"

    return {
        "mode": mode,
        "enabled": not policy_info.get("manual", True),
        "lock_exists": False,
        "read_error": False,
    }


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
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None

    if timezone.is_naive(parsed):
        try:
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        except Exception as exc:
            logger.warning("Failed to make timestamp aware in _parse_log_timestamp: %s", exc)
            return None
    return parsed


def _filter_recent_log_entries(
    entries: list[dict[str, object]], *, cutoff: datetime
) -> list[dict[str, object]]:
    """Return log entries that fall within the recent activity window."""

    return [
        entry
        for entry in entries
        if (
            (timestamp := entry.get("timestamp_raw"))
            and isinstance(timestamp, datetime)
            and timestamp >= cutoff
        )
    ]


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


def _build_auto_upgrade_report(
    *, limit: int = AUTO_UPGRADE_LOG_LIMIT, revision_info: dict[str, object] | None = None
) -> dict[str, object]:
    """Assemble the composite auto-upgrade report for the admin view."""

    base_dir = auto_upgrade_base_dir()
    policy_info = _load_upgrade_policy_report()
    log_info = _load_auto_upgrade_log_entries(base_dir, limit=limit)
    skip_revisions = _load_auto_upgrade_skip_revisions(base_dir)
    schedule_info = _load_auto_upgrade_schedule()

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

    schedule_disabled = schedule_info.get("enabled") is False
    if schedule_info.get("next_run") == str(_("Disabled")):
        schedule_disabled = True

    revision_details = _prepare_revision_info(revision_info)

    suite_details = _suite_uptime_details()
    suite_boot_time = suite_details.get("boot_time")
    suite_lock_started_at = suite_details.get("lock_started_at")

    settings_info = {
        "enabled": bool(not policy_info.get("manual", True)),
        "manual": bool(policy_info.get("manual", True)),
        "policies": policy_info.get("policies", []),
        "channels": policy_info.get("channels", []),
        "stable_only": bool(policy_info.get("stable_only", False)),
        "unstable_only": bool(policy_info.get("unstable_only", False)),
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
    recent_cutoff = timezone.localtime() - timedelta(hours=AUTO_UPGRADE_RECENT_ACTIVITY_HOURS)
    recent_log_entries = _filter_recent_log_entries(log_entries, cutoff=recent_cutoff)
    last_log_entry = recent_log_entries[0] if recent_log_entries else {}

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

    policy_error = policy_info.get("error")
    if policy_error:
        note(str(policy_error), severity="error")

    if settings_info.get("manual"):
        note(str(_("No upgrade policies apply; upgrades require manual action.")), severity="warning")

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
        "log_entries": recent_log_entries,
        "log_error": str(log_info.get("error", "")),
        "summary": summary,
        "recent_activity_hours": AUTO_UPGRADE_RECENT_ACTIVITY_HOURS,
        "failure_guide": auto_upgrade_failure_guide(),
    }


def _resolve_auto_upgrade_namespace(key: str) -> str | None:
    """Resolve sigils within the legacy ``AUTO-UPGRADE`` namespace."""

    normalized = key.replace("-", "_").upper()
    if normalized == "NEXT_CHECK":
        return _auto_upgrade_next_check()
    return None


def _trigger_upgrade_check(*, channel_override: str | None = None) -> bool:
    """Return ``True`` when the upgrade check was queued asynchronously."""

    def _run_sync_upgrade_check(channel_override: str | None = None) -> None:
        """Run the upgrade check synchronously with optional channel override."""

        if channel_override:
            check_github_updates(channel_override=channel_override, manual_trigger=True)
        else:
            check_github_updates(manual_trigger=True)

    broker_url = str(getattr(settings, "CELERY_BROKER_URL", "")).strip()
    if not broker_url or broker_url.startswith("memory://"):
        _run_sync_upgrade_check(channel_override)
        return False

    if not is_celery_enabled():
        _run_sync_upgrade_check(channel_override)
        return False

    if channel_override:
        queued = enqueue_task(
            check_github_updates,
            channel_override=channel_override,
            manual_trigger=True,
            require_enabled=False,
        )
    else:
        queued = enqueue_task(
            check_github_updates,
            manual_trigger=True,
            require_enabled=False,
        )

    if not queued:
        logger.warning(
            "Failed to enqueue upgrade check; running synchronously instead"
        )
        _run_sync_upgrade_check(channel_override)
        return False
    return True
