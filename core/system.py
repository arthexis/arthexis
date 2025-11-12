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
from django.template.response import TemplateResponse
from django.http import HttpResponseRedirect
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
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
from core import changelog as changelog_utils
from core.release import (
    _git_authentication_missing,
    _git_remote_url,
    _manager_git_credentials,
    _remote_with_credentials,
)
from core.tasks import check_github_updates
from core.models import Todo
from utils import revision


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


def _open_changelog_entries() -> list[dict[str, str]]:
    """Return changelog entries that are not yet part of a tagged release."""

    changelog_path = Path("CHANGELOG.rst")
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []

    collecting = False
    entries: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not collecting:
            if line == "Unreleased":
                collecting = True
            continue

        if not line:
            if entries:
                break
            continue

        if set(line) == {"-"}:
            # Underline immediately following the section heading.
            continue

        if not line.startswith("- "):
            break

        trimmed = line[2:].strip()
        if not trimmed:
            continue
        parts = trimmed.split(" ", 1)
        sha = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        entries.append({"sha": sha, "message": message, "url": _github_commit_url(sha)})

    return entries


def _latest_release_changelog() -> dict[str, object]:
    """Return the most recent tagged release entries for display."""

    changelog_path = Path("CHANGELOG.rst")
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {"title": "", "entries": []}

    lines = text.splitlines()
    state = "before"
    release_title = ""
    entries: list[dict[str, str]] = []
    seen_unreleased_entry = False

    for raw_line in lines:
        stripped = raw_line.strip()

        if state == "before":
            if stripped == "Unreleased":
                state = "unreleased-heading"
            continue

        if state == "unreleased-heading":
            # After encountering the heading underline we move into the
            # unreleased body, allowing for optional blank lines before the
            # first entry.
            state = "unreleased-body"
            if not stripped or set(stripped) == {"-"}:
                continue
            # Re-process the current line as part of the Unreleased body when
            # the underline is missing.

        if state == "unreleased-body":
            if not stripped:
                if seen_unreleased_entry:
                    state = "after-unreleased"
                continue
            if stripped.startswith("- "):
                seen_unreleased_entry = True
                continue
            # No entries were recorded in the Unreleased section; treat the
            # current line as the first release heading.
            release_title = stripped
            state = "release-heading"
            continue

        if state == "after-unreleased":
            if not stripped:
                continue
            release_title = stripped
            state = "release-heading"
            continue

        if state == "release-heading":
            state = "release-body"
            if not stripped or set(stripped) == {"-"}:
                continue
            # Allow the loop to treat the current line as the first body entry
            # when the underline is missing.

        if state == "release-body":
            if not stripped:
                if entries:
                    break
                continue
            if not stripped.startswith("- "):
                break
            trimmed = stripped[2:].strip()
            if not trimmed:
                continue
            parts = trimmed.split(" ", 1)
            sha = parts[0]
            message = parts[1] if len(parts) > 1 else ""
            entries.append({"sha": sha, "message": message, "url": _github_commit_url(sha)})

    return {"title": release_title, "entries": entries}


def _exclude_changelog_entries(shas: Iterable[str]) -> int:
    """Remove entries matching ``shas`` from the changelog.

    Returns the number of entries removed. Only entries within the
    ``Unreleased`` section are considered.
    """

    normalized_shas = {sha.strip() for sha in shas if sha and sha.strip()}
    if not normalized_shas:
        return 0

    changelog_path = Path("CHANGELOG.rst")
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return 0

    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    collecting = False
    removed = 0

    for raw_line in lines:
        stripped = raw_line.strip()

        if not collecting:
            new_lines.append(raw_line)
            if stripped == "Unreleased":
                collecting = True
            continue

        if not stripped:
            new_lines.append(raw_line)
            continue

        if set(stripped) == {"-"}:
            new_lines.append(raw_line)
            continue

        if not stripped.startswith("- "):
            new_lines.append(raw_line)
            collecting = False
            continue

        trimmed = stripped[2:].strip()
        if not trimmed:
            new_lines.append(raw_line)
            continue

        sha = trimmed.split(" ", 1)[0]
        if sha in normalized_shas:
            removed += 1
            normalized_shas.remove(sha)
            continue

        new_lines.append(raw_line)

    if removed:
        new_text = "".join(new_lines)
        if not new_text.endswith("\n"):
            new_text += "\n"
        changelog_path.write_text(new_text, encoding="utf-8")

    return removed


def _regenerate_changelog() -> None:
    """Rebuild the changelog file using recent git commits."""

    changelog_path = Path("CHANGELOG.rst")
    previous_text = (
        changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else None
    )
    range_spec = changelog_utils.determine_range_spec(previous_text=previous_text)
    sections = changelog_utils.collect_sections(
        range_spec=range_spec, previous_text=previous_text
    )
    content = changelog_utils.render_changelog(sections)
    if not content.endswith("\n"):
        content += "\n"
    changelog_path.write_text(content, encoding="utf-8")


def _format_git_command_output(
    command: list[str], result: subprocess.CompletedProcess[str]
) -> str:
    """Return a readable summary of a git command execution."""

    command_display = "$ " + " ".join(command)
    message_parts = []
    if result.stdout:
        message_parts.append(result.stdout.strip())
    if result.stderr:
        message_parts.append(result.stderr.strip())
    if result.returncode != 0:
        message_parts.append(f"[exit status {result.returncode}]")
    if message_parts:
        return command_display + "\n" + "\n".join(part for part in message_parts if part)
    return command_display


def _git_status() -> str:
    """Return the repository status after attempting to commit."""

    status_result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = status_result.stdout.strip()
    stderr = status_result.stderr.strip()
    if stdout and stderr:
        return stdout + "\n" + stderr
    return stdout or stderr


def _commit_changelog() -> tuple[bool, str, str]:
    """Stage, commit, and push the changelog file."""

    def _retry_push_with_release_credentials(
        command: list[str],
        result: subprocess.CompletedProcess[str],
    ) -> bool:
        exc = subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
        if not _git_authentication_missing(exc):
            return False

        creds = _manager_git_credentials()
        if not creds or not creds.has_auth():
            return False

        remote_url = _git_remote_url("origin")
        if not remote_url:
            return False

        authed_url = _remote_with_credentials(remote_url, creds)
        if not authed_url:
            return False

        retry_command = ["git", "push", authed_url]
        retry_result = subprocess.run(
            retry_command,
            capture_output=True,
            text=True,
            check=False,
        )
        formatted_retry = _format_git_command_output(retry_command, retry_result)
        if formatted_retry:
            outputs.append(formatted_retry)
        logger.info(
            "Executed %s with exit code %s",
            retry_command,
            retry_result.returncode,
        )
        return retry_result.returncode == 0

    git_commands: list[list[str]] = [
        ["git", "add", "CHANGELOG.rst"],
        [
            "git",
            "commit",
            "-m",
            "chore: update changelog",
            "--",
            "CHANGELOG.rst",
        ],
        ["git", "push"],
    ]
    outputs: list[str] = []
    success = True

    for command in git_commands:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )
        formatted = _format_git_command_output(command, result)
        outputs.append(formatted)
        logger.info("Executed %s with exit code %s", command, result.returncode)
        if result.returncode != 0:
            if command[:2] == ["git", "push"] and _retry_push_with_release_credentials(
                command, result
            ):
                continue
            success = False
            break

    command_output = "\n\n".join(output for output in outputs if output)
    repo_status = _git_status()
    return success, command_output, repo_status


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


def _system_changelog_report_view(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "exclude":
            selected_shas = request.POST.getlist("selected_shas")
            removed = _exclude_changelog_entries(selected_shas)
            if removed:
                messages.success(
                    request,
                    ngettext(
                        "Excluded %(count)d changelog entry.",
                        "Excluded %(count)d changelog entries.",
                        removed,
                    )
                    % {"count": removed},
                )
            else:
                if selected_shas:
                    messages.info(
                        request,
                        _(
                            "The selected changelog entries were not found or have already been excluded."
                        ),
                    )
                else:
                    messages.info(
                        request,
                        _("Select at least one changelog entry to exclude."),
                    )
        elif action == "commit":
            success, command_output, repo_status = _commit_changelog()
            details: list[str] = []
            if command_output:
                details.append(
                    format_html(
                        "<div class=\"changelog-git-output\"><strong>{}</strong><pre>{}</pre></div>",
                        _("Command log"),
                        command_output,
                    )
                )
            if repo_status:
                details.append(
                    format_html(
                        "<div class=\"changelog-git-status\"><strong>{}</strong><pre>{}</pre></div>",
                        _("Repository status"),
                        repo_status,
                    )
                )
            details_html = (
                format_html_join("", "{}", ((detail,) for detail in details))
                if details
                else ""
            )
            if success:
                base_message = _("Committed the changelog and pushed to the current branch.")
                messages.success(request, format_html("{}{}", base_message, details_html))
            else:
                base_message = _("Unable to commit the changelog.")
                messages.error(request, format_html("{}{}", base_message, details_html))
        else:
            try:
                _regenerate_changelog()
            except subprocess.CalledProcessError as exc:
                logger.exception("Changelog regeneration failed")
                messages.error(
                    request,
                    _("Unable to recalculate the changelog: %(error)s")
                    % {"error": exc.stderr.strip() if exc.stderr else str(exc)},
                )
            except Exception as exc:  # pragma: no cover - unexpected failure
                logger.exception("Unexpected error while regenerating changelog")
                messages.error(
                    request,
                    _("Unable to recalculate the changelog: %(error)s")
                    % {"error": str(exc)},
                )
            else:
                messages.success(
                    request,
                    _("Successfully recalculated the changelog from recent commits."),
                )
        return HttpResponseRedirect(reverse("admin:system-changelog-report"))

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Changelog Report"),
            "open_changelog_entries": _open_changelog_entries(),
            "latest_release_changelog": _latest_release_changelog(),
        }
    )
    return TemplateResponse(request, "admin/system_changelog_report.html", context)


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
                "system/changelog-report/",
                admin.site.admin_view(_system_changelog_report_view),
                name="system-changelog-report",
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

