from __future__ import annotations

import logging
import shutil
import re
import shlex
import subprocess
import time
from pathlib import Path
import urllib.error
import urllib.request

from celery import shared_task
from core import github_issues
from core.auto_upgrade_failover import clear_failover_lock, write_failover_lock
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone


AUTO_UPGRADE_HEALTH_DELAY_SECONDS = 30
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME = "auto_upgrade_network_failures.lck"
AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD = 3

_NETWORK_FAILURE_PATTERNS = (
    "could not resolve host",
    "couldn't resolve host",
    "failed to connect",
    "connection timed out",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "could not resolve proxy",
    "no route to host",
)

SEVERITY_NORMAL = "normal"
SEVERITY_LOW = "low"
SEVERITY_CRITICAL = "critical"

_PackageReleaseModel = None


def _get_package_release_model():
    """Return the :class:`core.models.PackageRelease` model when available."""

    global _PackageReleaseModel

    if _PackageReleaseModel is not None:
        return _PackageReleaseModel

    try:
        from core.models import PackageRelease  # noqa: WPS433 - runtime import
    except Exception:  # pragma: no cover - app registry not ready
        return None

    _PackageReleaseModel = PackageRelease
    return PackageRelease


model = _get_package_release_model()
if model is not None:  # pragma: no branch - runtime constant setup
    SEVERITY_NORMAL = model.Severity.NORMAL
    SEVERITY_LOW = model.Severity.LOW
    SEVERITY_CRITICAL = model.Severity.CRITICAL


logger = logging.getLogger(__name__)


@shared_task
def heartbeat() -> None:
    """Log a simple heartbeat message."""
    logger.info("Heartbeat task executed")


@shared_task
def renew_ssl_certificate(force: bool = False) -> None:
    """Execute the renew-certs helper script to refresh the node SSL cert."""

    base_dir = _project_base_dir()
    script = base_dir / "renew-certs.sh"
    if not script.exists():
        raise FileNotFoundError(f"Certificate renewal script not found at {script}")

    args = [str(script)]
    if force:
        args.append("--force")

    logger.info("Running %s", " ".join(shlex.quote(arg) for arg in args))
    subprocess.run(args, cwd=base_dir, check=True)


def _auto_upgrade_log_path(base_dir: Path) -> Path:
    """Return the log file used for auto-upgrade events."""

    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "auto-upgrade.log"


def _project_base_dir() -> Path:
    """Return the filesystem base directory for runtime operations."""

    base_dir = getattr(settings, "BASE_DIR", None)
    if not base_dir:
        return Path(__file__).resolve().parent.parent
    if isinstance(base_dir, Path):
        return base_dir
    return Path(str(base_dir))


def _append_auto_upgrade_log(base_dir: Path, message: str) -> None:
    """Append ``message`` to the auto-upgrade log, ignoring errors."""

    try:
        log_file = _auto_upgrade_log_path(base_dir)
        timestamp = timezone.now().isoformat()
        with log_file.open("a") as fh:
            fh.write(f"{timestamp} {message}\n")
    except Exception:  # pragma: no cover - best effort logging only
        logger.warning("Failed to append auto-upgrade log entry: %s", message)


def _systemctl_command() -> list[str]:
    """Return the base systemctl command, preferring sudo when available."""

    if shutil.which("systemctl") is None:
        return []
    if shutil.which("sudo") is not None:
        return ["sudo", "systemctl"]
    return ["systemctl"]


def _wait_for_service_restart(
    base_dir: Path, service: str, timeout: int = 30
) -> bool:
    """Return ``True`` when ``service`` reports active within ``timeout`` seconds."""

    if not service:
        return True

    command = _systemctl_command()
    if not command:
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [*command, "is-active", "--quiet", service],
            cwd=base_dir,
            check=False,
        )
        if result.returncode == 0:
            return True
        time.sleep(2)

    subprocess.run(
        [*command, "status", service, "--no-pager"],
        cwd=base_dir,
        check=False,
    )
    return False


def _revert_after_restart_failure(base_dir: Path, service: str) -> None:
    """Revert the latest upgrade when ``service`` fails to restart."""

    _append_auto_upgrade_log(
        base_dir,
        (
            f"Service {service or 'unknown'} failed to restart after upgrade; "
            "reverting to failover branch"
        ),
    )
    try:
        subprocess.run(["./upgrade.sh", "--revert"], cwd=base_dir, check=True)
    except subprocess.CalledProcessError:
        logger.exception("Automatic revert failed after restart failure")
        _append_auto_upgrade_log(
            base_dir,
            "Automatic revert after restart failure was unsuccessful; manual intervention required",
        )
        return

    revision = _current_revision(base_dir)
    write_failover_lock(
        base_dir,
        reason=f"Service {service or 'unknown'} failed to restart after upgrade",
        detail=(
            "Restart verification did not succeed; the node was returned to the "
            "failover snapshot."
        ),
        revision=revision or None,
    )

    command = _systemctl_command()
    if not command or not service:
        return

    try:
        subprocess.run([*command, "restart", service], cwd=base_dir, check=True)
    except subprocess.CalledProcessError:
        logger.exception("Failed to restart %s after reverting to failover branch", service)
        _append_auto_upgrade_log(
            base_dir,
            (
                f"Restart after reverting to failover branch failed for {service}; "
                "manual intervention required"
            ),
        )
        return

    if _wait_for_service_restart(base_dir, service):
        _append_auto_upgrade_log(
            base_dir,
            f"Service {service} restarted successfully from failover branch",
        )
    else:
        _append_auto_upgrade_log(
            base_dir,
            (
                f"Service {service} is still inactive after reverting; "
                "manual intervention required"
            ),
        )


def _resolve_release_severity(version: str | None) -> str:
    """Return the stored severity for *version*, defaulting to normal."""

    if not version:
        return SEVERITY_NORMAL

    model = _get_package_release_model()
    if model is None:
        return SEVERITY_NORMAL

    try:
        queryset = model.objects.filter(version=version)
        release = (
            queryset.filter(package__is_active=True).first() or queryset.first()
        )
    except DatabaseError:  # pragma: no cover - depends on DB availability
        return SEVERITY_NORMAL

    if not release:
        return SEVERITY_NORMAL

    severity = getattr(release, "severity", None)
    if not severity:
        return SEVERITY_NORMAL
    return severity


def _read_local_version(base_dir: Path) -> str | None:
    """Return the local VERSION file contents when readable."""

    version_path = base_dir / "VERSION"
    if not version_path.exists():
        return None
    try:
        return version_path.read_text().strip()
    except OSError:  # pragma: no cover - filesystem error
        return None


def _read_remote_version(base_dir: Path, branch: str) -> str | None:
    """Return the VERSION file from ``origin/<branch>`` when available."""

    try:
        return (
            subprocess.check_output(
                [
                    "git",
                    "show",
                    f"origin/{branch}:VERSION",
                ],
                cwd=base_dir,
                stderr=subprocess.STDOUT,
                text=True,
            )
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover - git failure
        return None


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


def _network_failure_lock_path(base_dir: Path) -> Path:
    return base_dir / "locks" / AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME


def _read_network_failure_count(base_dir: Path) -> int:
    lock_path = _network_failure_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return 0
    except OSError:
        logger.warning("Failed to read auto-upgrade network failure lockfile")
        return 0
    if not raw_value:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid auto-upgrade network failure lockfile contents: %s", raw_value
        )
        return 0


def _write_network_failure_count(base_dir: Path, count: int) -> None:
    lock_path = _network_failure_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(count), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade network failure lockfile")


def _reset_network_failure_count(base_dir: Path) -> None:
    lock_path = _network_failure_lock_path(base_dir)
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        logger.warning("Failed to remove auto-upgrade network failure lockfile")


def _extract_error_output(exc: subprocess.CalledProcessError) -> str:
    parts: list[str] = []
    for attr in ("stderr", "stdout", "output"):
        value = getattr(exc, attr, None)
        if not value:
            continue
        if isinstance(value, bytes):
            try:
                value = value.decode()
            except Exception:  # pragma: no cover - best effort decoding
                value = value.decode(errors="ignore")
        parts.append(str(value))
    detail = " ".join(part.strip() for part in parts if part)
    if not detail:
        detail = str(exc)
    return detail


def _is_network_failure(exc: subprocess.CalledProcessError) -> bool:
    command = exc.cmd
    if isinstance(command, (list, tuple)):
        if not command:
            return False
        first = str(command[0])
    else:
        command_str = str(command)
        first = command_str.split()[0] if command_str else ""
    if "git" not in first:
        return False
    detail = _extract_error_output(exc).lower()
    return any(pattern in detail for pattern in _NETWORK_FAILURE_PATTERNS)


def _record_network_failure(base_dir: Path, detail: str) -> int:
    count = _read_network_failure_count(base_dir) + 1
    _write_network_failure_count(base_dir, count)
    _append_auto_upgrade_log(
        base_dir,
        f"Auto-upgrade network failure {count}: {detail}",
    )
    return count


def _charge_point_active(base_dir: Path) -> bool:
    lock_path = base_dir / "locks" / "charging.lck"
    if lock_path.exists():
        return True
    try:
        from ocpp import store  # type: ignore
    except Exception:
        return False
    try:
        connections = getattr(store, "connections", {})
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(connections)


def _trigger_auto_upgrade_reboot(base_dir: Path) -> None:
    try:
        subprocess.run(["sudo", "systemctl", "reboot"], check=False)
    except Exception:  # pragma: no cover - best effort reboot command
        logger.exception(
            "Failed to trigger reboot after repeated auto-upgrade network failures"
        )


def _reboot_if_no_charge_point(base_dir: Path) -> None:
    if _charge_point_active(base_dir):
        _append_auto_upgrade_log(
            base_dir,
            "Skipping reboot after repeated auto-upgrade network failures; a charge point is active",
        )
        return
    _append_auto_upgrade_log(
        base_dir,
        "Rebooting due to repeated auto-upgrade network failures",
    )
    _trigger_auto_upgrade_reboot(base_dir)


def _handle_network_failure_if_applicable(
    base_dir: Path, exc: subprocess.CalledProcessError
) -> bool:
    if not _is_network_failure(exc):
        return False
    detail = _extract_error_output(exc)
    failure_count = _record_network_failure(base_dir, detail)
    if failure_count >= AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD:
        _reboot_if_no_charge_point(base_dir)
    return True


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
    port = 8888
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
def check_github_updates(channel_override: str | None = None) -> None:
    """Check the GitHub repo for updates and upgrade if needed."""
    base_dir = _project_base_dir()
    mode_file = base_dir / "locks" / "auto_upgrade.lck"
    mode = "version"
    reset_network_failures = True
    try:
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

        override_mode = None
        if channel_override:
            requested = channel_override.strip().lower()
            if requested in {"latest", "stable"}:
                override_mode = requested
            elif requested == "normal":
                override_mode = None
        if override_mode:
            mode = override_mode

        branch = "main"
        try:
            subprocess.run(
                ["git", "fetch", "origin", branch],
                cwd=base_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            if _handle_network_failure_if_applicable(base_dir, exc):
                reset_network_failures = False
            raise

        log_file = _auto_upgrade_log_path(base_dir)
        with log_file.open("a") as fh:
            fh.write(
                f"{timezone.now().isoformat()} check_github_updates triggered\n"
            )

        if override_mode:
            _append_auto_upgrade_log(
                base_dir,
                f"Using admin override channel: {override_mode}",
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

        try:
            remote_revision = subprocess.check_output(
                ["git", "rev-parse", f"origin/{branch}"],
                cwd=base_dir,
                stderr=subprocess.STDOUT,
                text=True,
            ).strip()
        except subprocess.CalledProcessError as exc:
            if _handle_network_failure_if_applicable(base_dir, exc):
                reset_network_failures = False
            raise

        skipped_revisions = _load_skipped_revisions(base_dir)
        if remote_revision in skipped_revisions:
            _append_auto_upgrade_log(
                base_dir,
                f"Skipping auto-upgrade for blocked revision {remote_revision}",
            )
            if startup:
                startup()
            return

        remote_version = _read_remote_version(base_dir, branch)
        local_version = _read_local_version(base_dir)
        remote_severity = _resolve_release_severity(remote_version)

        upgrade_stamp = timezone.now().strftime("@ %Y%m%d %H:%M")

        upgrade_was_applied = False

        if mode == "latest":
            local_revision = (
                subprocess.check_output(
                    ["git", "rev-parse", branch],
                    cwd=base_dir,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                .strip()
            )
            if local_revision == remote_revision:
                if startup:
                    startup()
                return

            if (
                remote_version
                and local_version
                and remote_version != local_version
                and remote_severity == SEVERITY_LOW
                and _shares_stable_series(local_version, remote_version)
            ):
                _append_auto_upgrade_log(
                    base_dir,
                    f"Skipping auto-upgrade for low severity patch {remote_version}",
                )
                if startup:
                    startup()
                return

            if notify:
                notify("Upgrading...", upgrade_stamp)
            args = ["./upgrade.sh", "--latest", "--no-restart"]
            upgrade_was_applied = True
        else:
            local_value = local_version or "0"
            remote_value = remote_version or local_value

            if local_value == remote_value:
                if startup:
                    startup()
                return

            if (
                mode == "stable"
                and local_version
                and remote_version
                and remote_version != local_version
                and _shares_stable_series(local_version, remote_version)
                and remote_severity != SEVERITY_CRITICAL
            ):
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
            if service:
                _append_auto_upgrade_log(
                    base_dir,
                    f"Waiting for {service} to restart after upgrade",
                )
                if not _wait_for_service_restart(base_dir, service):
                    _revert_after_restart_failure(base_dir, service)
                    return
                _append_auto_upgrade_log(
                    base_dir,
                    f"Service {service} restarted successfully after upgrade",
                )
            else:
                _append_auto_upgrade_log(
                    base_dir,
                    "Service restart requested but service lock was empty; skipping automatic verification",
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
    finally:
        if reset_network_failures:
            _reset_network_failure_count(base_dir)


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


def _current_revision(base_dir: Path) -> str:
    """Return the current git revision when available."""

    try:
        output = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base_dir)
    except Exception:  # pragma: no cover - best effort capture
        return ""

    if isinstance(output, bytes):
        try:
            return output.decode().strip()
        except Exception:  # pragma: no cover - defensive decoding
            return output.decode(errors="ignore").strip()

    return str(output).strip()


def _handle_failed_health_check(base_dir: Path, detail: str) -> None:
    revision = _current_revision(base_dir)
    if not revision:
        logger.warning("Failed to determine revision during auto-upgrade revert")

    _add_skipped_revision(base_dir, revision)
    _append_auto_upgrade_log(base_dir, "Health check failed; reverting upgrade")
    subprocess.run(["./upgrade.sh", "--revert"], cwd=base_dir, check=True)
    write_failover_lock(
        base_dir,
        reason="Auto-upgrade health check failed",
        detail=detail,
        revision=revision or None,
    )


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
        clear_failover_lock(base_dir)
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


@shared_task
def ensure_recurring_client_reports() -> None:
    """Ensure scheduled consumer reports run for the current period."""

    from core.models import ClientReportSchedule

    reference = timezone.localdate()
    schedules = ClientReportSchedule.objects.filter(
        periodicity__in=[
            ClientReportSchedule.PERIODICITY_DAILY,
            ClientReportSchedule.PERIODICITY_WEEKLY,
            ClientReportSchedule.PERIODICITY_MONTHLY,
        ]
    ).prefetch_related("chargers")

    for schedule in schedules:
        try:
            schedule.generate_missing_reports(reference=reference)
        except Exception:
            logger.exception(
                "Automatic consumer report generation failed for schedule %s",
                schedule.pk,
            )
