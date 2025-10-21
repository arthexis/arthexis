from __future__ import annotations

import logging
import shutil
import re
import subprocess
from pathlib import Path
import urllib.error
import urllib.request

from celery import shared_task
from core import github_issues
from django.utils import timezone


AUTO_UPGRADE_HEALTH_DELAY_SECONDS = 30
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"


logger = logging.getLogger(__name__)


@shared_task
def heartbeat() -> None:
    """Log a simple heartbeat message."""
    logger.info("Heartbeat task executed")


def _auto_upgrade_log_path(base_dir: Path) -> Path:
    """Return the log file used for auto-upgrade events."""

    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "auto-upgrade.log"


def _append_auto_upgrade_log(base_dir: Path, message: str) -> None:
    """Append ``message`` to the auto-upgrade log, ignoring errors."""

    try:
        log_file = _auto_upgrade_log_path(base_dir)
        timestamp = timezone.now().isoformat()
        with log_file.open("a") as fh:
            fh.write(f"{timestamp} {message}\n")
    except Exception:  # pragma: no cover - best effort logging only
        logger.warning("Failed to append auto-upgrade log entry: %s", message)


def _skip_lock_path(base_dir: Path) -> Path:
    return base_dir / "locks" / AUTO_UPGRADE_SKIP_LOCK_NAME


def _load_skipped_revisions(base_dir: Path) -> set[str]:
    skip_file = _skip_lock_path(base_dir)
    try:
        return {
            line.strip()
            for line in skip_file.read_text().splitlines()
            if line.strip()
        }
    except FileNotFoundError:
        return set()
    except OSError:
        logger.warning("Failed to read auto-upgrade skip lockfile")
        return set()


def _add_skipped_revision(base_dir: Path, revision: str) -> None:
    if not revision:
        return

    skip_file = _skip_lock_path(base_dir)
    try:
        skip_file.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_skipped_revisions(base_dir)
        if revision in existing:
            return
        with skip_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{revision}\n")
        _append_auto_upgrade_log(
            base_dir, f"Recorded blocked revision {revision} for auto-upgrade"
        )
    except OSError:
        logger.warning(
            "Failed to update auto-upgrade skip lockfile with revision %s", revision
        )


def _resolve_service_url(base_dir: Path) -> str:
    """Return the local URL used to probe the Django suite."""

    lock_dir = base_dir / "locks"
    mode_file = lock_dir / "nginx_mode.lck"
    mode = "internal"
    if mode_file.exists():
        try:
            value = mode_file.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            mode = value.lower()
    port = 8000 if mode == "public" else 8888
    return f"http://127.0.0.1:{port}/"


def _parse_major_minor(version: str) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _shares_stable_series(local: str, remote: str) -> bool:
    local_parts = _parse_major_minor(local)
    remote_parts = _parse_major_minor(remote)
    if not local_parts or not remote_parts:
        return False
    return local_parts == remote_parts


@shared_task
def check_github_updates() -> None:
    """Check the GitHub repo for updates and upgrade if needed."""
    base_dir = Path(__file__).resolve().parent.parent
    mode_file = base_dir / "locks" / "auto_upgrade.lck"
    mode = "version"
    if mode_file.exists():
        try:
            raw_mode = mode_file.read_text().strip()
        except (OSError, UnicodeDecodeError):
            logger.warning(
                "Failed to read auto-upgrade mode lockfile", exc_info=True
            )
        else:
            cleaned_mode = raw_mode.lower()
            if cleaned_mode:
                mode = cleaned_mode

    branch = "main"
    subprocess.run(["git", "fetch", "origin", branch], cwd=base_dir, check=True)

    log_file = _auto_upgrade_log_path(base_dir)
    with log_file.open("a") as fh:
        fh.write(
            f"{timezone.now().isoformat()} check_github_updates triggered\n"
        )

    notify = None
    startup = None
    try:  # pragma: no cover - optional dependency
        from core.notifications import notify  # type: ignore
    except Exception:
        notify = None
    try:  # pragma: no cover - optional dependency
        from nodes.apps import _startup_notification as startup  # type: ignore
    except Exception:
        startup = None

    remote_revision = (
        subprocess.check_output(
            ["git", "rev-parse", f"origin/{branch}"], cwd=base_dir
        )
        .decode()
        .strip()
    )

    skipped_revisions = _load_skipped_revisions(base_dir)
    if remote_revision in skipped_revisions:
        _append_auto_upgrade_log(
            base_dir, f"Skipping auto-upgrade for blocked revision {remote_revision}"
        )
        if startup:
            startup()
        return

    upgrade_stamp = timezone.now().strftime("@ %Y%m%d %H:%M")

    upgrade_was_applied = False

    if mode == "latest":
        local = (
            subprocess.check_output(["git", "rev-parse", branch], cwd=base_dir)
            .decode()
            .strip()
        )
        if local == remote_revision:
            if startup:
                startup()
            return
        if notify:
            notify("Upgrading...", upgrade_stamp)
        args = ["./upgrade.sh", "--latest", "--no-restart"]
        upgrade_was_applied = True
    else:
        local = "0"
        version_file = base_dir / "VERSION"
        if version_file.exists():
            local = version_file.read_text().strip()
        remote = (
            subprocess.check_output(
                [
                    "git",
                    "show",
                    f"origin/{branch}:VERSION",
                ],
                cwd=base_dir,
            )
            .decode()
            .strip()
        )
        if local == remote:
            if startup:
                startup()
            return
        if mode == "stable" and _shares_stable_series(local, remote):
            if startup:
                startup()
            return
        if notify:
            notify("Upgrading...", upgrade_stamp)
        if mode == "stable":
            args = ["./upgrade.sh", "--stable", "--no-restart"]
        else:
            args = ["./upgrade.sh", "--no-restart"]
        upgrade_was_applied = True

    with log_file.open("a") as fh:
        fh.write(
            f"{timezone.now().isoformat()} running: {' '.join(args)}\n"
        )

    subprocess.run(args, cwd=base_dir, check=True)

    service_file = base_dir / "locks/service.lck"
    if service_file.exists():
        service = service_file.read_text().strip()
        subprocess.run(
            [
                "sudo",
                "systemctl",
                "kill",
                "--signal=TERM",
                service,
            ]
        )
    else:
        subprocess.run(["pkill", "-f", "manage.py runserver"])

    if upgrade_was_applied:
        _append_auto_upgrade_log(
            base_dir,
            (
                "Scheduled post-upgrade health check in %s seconds"
                % AUTO_UPGRADE_HEALTH_DELAY_SECONDS
            ),
        )
        _schedule_health_check(1)


@shared_task
def poll_email_collectors() -> None:
    """Poll all configured email collectors for new messages."""
    try:
        from .models import EmailCollector
    except Exception:  # pragma: no cover - app not ready
        return

    for collector in EmailCollector.objects.all():
        collector.collect()


@shared_task
def report_runtime_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
    fingerprint: str | None = None,
):
    """Report a runtime issue to GitHub using :mod:`core.github_issues`."""

    try:
        response = github_issues.create_issue(
            title,
            body,
            labels=labels,
            fingerprint=fingerprint,
        )
    except Exception:
        logger.exception("Failed to report runtime issue '%s'", title)
        raise

    if response is None:
        logger.info("Skipped GitHub issue creation for fingerprint %s", fingerprint)
    else:
        logger.info("Reported runtime issue '%s' to GitHub", title)

    return response


def _record_health_check_result(
    base_dir: Path, attempt: int, status: int | None, detail: str
) -> None:
    status_display = status if status is not None else "unreachable"
    message = "Health check attempt %s %s (%s)" % (attempt, detail, status_display)
    _append_auto_upgrade_log(base_dir, message)


def _schedule_health_check(next_attempt: int) -> None:
    verify_auto_upgrade_health.apply_async(
        kwargs={"attempt": next_attempt},
        countdown=AUTO_UPGRADE_HEALTH_DELAY_SECONDS,
    )


def _handle_failed_health_check(base_dir: Path, detail: str) -> None:
    revision = ""
    try:
        revision = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base_dir)
            .decode()
            .strip()
        )
    except Exception:  # pragma: no cover - best effort capture
        logger.warning("Failed to determine revision during auto-upgrade revert")

    _add_skipped_revision(base_dir, revision)
    _append_auto_upgrade_log(base_dir, "Health check failed; reverting upgrade")
    subprocess.run(["./upgrade.sh", "--revert"], cwd=base_dir, check=True)


@shared_task
def verify_auto_upgrade_health(attempt: int = 1) -> bool | None:
    """Verify the upgraded suite responds successfully.

    After the post-upgrade delay the site is probed once; any response other
    than HTTP 200 triggers an automatic revert and records the failing
    revision so future upgrade attempts skip it.
    """

    base_dir = Path(__file__).resolve().parent.parent
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


@shared_task
def run_client_report_schedule(schedule_id: int) -> None:
    """Execute a :class:`core.models.ClientReportSchedule` run."""

    from core.models import ClientReportSchedule

    schedule = ClientReportSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        logger.warning("ClientReportSchedule %s no longer exists", schedule_id)
        return

    try:
        schedule.run()
    except Exception:
        logger.exception("ClientReportSchedule %s failed", schedule_id)
        raise
