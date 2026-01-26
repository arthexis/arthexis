from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from celery import shared_task
from .auto_upgrade import append_auto_upgrade_log, _add_skipped_revision, _record_auto_upgrade_failure
from .utils import _current_revision, _project_base_dir


logger = logging.getLogger(__name__)


def _is_migration_server_running(lock_dir: Path) -> bool:
    """Return ``True`` when the migration server lock indicates it is active."""

    state_path = lock_dir / "migration_server.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except json.JSONDecodeError:
        return True

    pid = payload.get("pid")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int):
        return False

    from apps.core import tasks as core_tasks

    cmdline = core_tasks._read_process_cmdline(pid)
    script_path = lock_dir.parent / "scripts" / "migration_server.py"
    if not any(str(part) == str(script_path) for part in cmdline):
        return False

    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str):
        try:
            timestamp = float(timestamp)
        except ValueError:
            timestamp = None

    start_time = core_tasks._read_process_start_time(pid)
    if (
        isinstance(timestamp, (int, float))
        and start_time is not None
        and abs(start_time - timestamp) > 120
    ):
        return False

    return True


@shared_task
def poll_emails() -> None:
    """Poll all configured email collectors for new messages."""
    try:
        from apps.emails.models import EmailCollector
    except Exception:  # pragma: no cover - app not ready
        return

    for collector in EmailCollector.objects.all():
        collector.collect()


@shared_task(name="apps.core.tasks.poll_emails")
def legacy_poll_emails() -> None:
    """Backward-compatible alias for the email polling task."""

    poll_emails()


def _record_health_check_result(
    base_dir: Path, attempt: int, status: int | None, detail: str
) -> None:
    status_display = status if status is not None else "unreachable"
    message = "Health check attempt %s %s (%s)" % (attempt, detail, status_display)
    append_auto_upgrade_log(base_dir, message)


def _resolve_service_url(base_dir: Path) -> str:
    """Return the local URL used to probe the Django suite."""

    lock_dir = base_dir / ".locks"
    mode_file = lock_dir / "nginx_mode.lck"
    mode = "internal"
    if mode_file.exists():
        try:
            value = mode_file.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            mode = value.lower()
    port = 8888
    return f"http://127.0.0.1:{port}/"


def _handle_failed_health_check(base_dir: Path, detail: str) -> None:
    revision = _current_revision(base_dir)
    if not revision:
        logger.warning(
            "Failed to determine revision during auto-upgrade health check failure"
        )

    _add_skipped_revision(base_dir, revision)
    append_auto_upgrade_log(
        base_dir, "Health check failed; manual intervention required"
    )
    _record_auto_upgrade_failure(base_dir, detail or "Health check failed")


@shared_task
def verify_auto_upgrade_health(attempt: int = 1) -> bool | None:
    """Verify the upgraded suite responds successfully.

    After the post-upgrade delay the site is probed once; any response other
    than HTTP 200 triggers an automatic revert and records the failing
    revision so future upgrade attempts skip it.
    """

    base_dir = _project_base_dir()
    url = _resolve_service_url(base_dir)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Arthexis-AutoUpgrade/1.0"},
    )

    status: int | None = None
    detail = "succeeded"
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = getattr(response, "status", response.getcode())
    except urllib.error.HTTPError as exc:
        status = exc.code
        detail = f"returned HTTP {exc.code}"
        logger.warning(
            "Auto-upgrade health check attempt %s returned HTTP %s", attempt, exc.code
        )
    except urllib.error.URLError as exc:
        detail = f"failed with {exc}"
        logger.warning(
            "Auto-upgrade health check attempt %s failed: %s", attempt, exc
        )
    except Exception as exc:  # pragma: no cover - unexpected network error
        detail = f"failed with {exc}"
        logger.exception(
            "Unexpected error probing suite during auto-upgrade attempt %s", attempt
        )
        _record_health_check_result(base_dir, attempt, status, detail)
        _handle_failed_health_check(base_dir, detail)
        return False

    if status == 200:
        _record_health_check_result(base_dir, attempt, status, "succeeded")
        logger.info(
            "Auto-upgrade health check succeeded on attempt %s with HTTP %s",
            attempt,
            status,
        )
        return True

    if detail == "succeeded":
        if status is not None:
            detail = f"returned HTTP {status}"
        else:
            detail = "failed with unknown status"

    _record_health_check_result(base_dir, attempt, status, detail)
    _handle_failed_health_check(base_dir, detail)
    return False


@shared_task(name="apps.core.tasks.verify_auto_upgrade_health")
def legacy_verify_auto_upgrade_health(attempt: int = 1) -> bool | None:
    """Backward-compatible alias for the auto-upgrade health check task."""

    return verify_auto_upgrade_health(attempt=attempt)


@shared_task
def run_client_report_schedule(schedule_id: int) -> None:
    """Execute a :class:`core.models.ClientReportSchedule` run."""

    from apps.energy.models import ClientReportSchedule

    schedule = ClientReportSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        logger.warning("ClientReportSchedule %s no longer exists", schedule_id)
        return

    try:
        schedule.run()
    except Exception:
        logger.exception("ClientReportSchedule %s failed", schedule_id)
        raise


@shared_task(name="apps.core.tasks.run_client_report_schedule")
def legacy_run_client_report_schedule(schedule_id: int) -> None:
    """Backward-compatible alias for the client report schedule task."""

    run_client_report_schedule(schedule_id)
