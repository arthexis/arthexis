from __future__ import annotations

import logging
import os
import shutil
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path
import urllib.error
import urllib.request

from celery import shared_task
from core import github_issues
from . import release_workflow
from core.auto_upgrade_failover import clear_failover_lock, write_failover_lock
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone


AUTO_UPGRADE_HEALTH_DELAY_SECONDS = 300
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME = "auto_upgrade_network_failures.lck"
AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD = 3
WATCH_UPGRADE_BINARY = Path("/usr/local/bin/watch-upgrade")

_NETWORK_FAILURE_PATTERNS = (
    "could not resolve host",
    "couldn't resolve host",
    "failed to connect",
    "couldn't connect to server",
    "connection reset by peer",
    "recv failure",
    "connection timed out",
    "network is unreachable",
    "temporary failure in name resolution",
    "tls connection was non-properly terminated",
    "gnutls recv error",
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


def _run_upgrade_command(base_dir: Path, args: list[str]) -> tuple[str | None, bool]:
    """Run the upgrade script, detaching from system services when possible.

    Returns a tuple of ``(unit_name, ran_inline)`` where ``unit_name`` is set
    when the upgrade was delegated to a transient systemd unit and
    ``ran_inline`` indicates whether the upgrade script was executed inline.
    """

    def _systemd_run_command() -> list[str]:
        binary = shutil.which("systemd-run")
        if not binary:
            return []

        sudo_path = shutil.which("sudo")
        if not sudo_path:
            return [binary]

        try:
            sudo_ready = subprocess.run(
                [sudo_path, "-n", "true"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            sudo_ready = None

        if sudo_ready is not None and sudo_ready.returncode == 0:
            return [sudo_path, "-n", binary]

        return [binary]

    def _read_service_name() -> str:
        lock_path = base_dir / "locks" / "service.lck"
        try:
            value = lock_path.read_text().strip()
        except OSError:
            return ""
        return value

    systemd_run_command = _systemd_run_command()
    running_in_service = bool(os.environ.get("INVOCATION_ID"))
    service_name = _read_service_name()

    if systemd_run_command and running_in_service and service_name and WATCH_UPGRADE_BINARY.exists():
        unit_name = f"upgrade-watcher-{uuid.uuid4().hex}"
        detached_args = [
            *systemd_run_command,
            "--unit",
            unit_name,
            "--description",
            f"Watch {service_name} upgrade",
            str(WATCH_UPGRADE_BINARY),
            service_name,
        ]

        def _format_detached_failure(
            result: Exception | subprocess.CompletedProcess[str],
        ) -> str:
            if isinstance(result, subprocess.CompletedProcess):
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()
                output = stderr or stdout
                if output:
                    return f"exit code {result.returncode}; {output}"
                return f"exit code {result.returncode}; no output captured"

            if isinstance(result, subprocess.CalledProcessError):
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()
                output = stderr or stdout
                if output:
                    return f"exit code {result.returncode}; {output}"
                return f"exit code {result.returncode}; no output captured"

            return str(result)

        try:
            _append_auto_upgrade_log(
                base_dir,
                (
                    "Delegating auto-upgrade to transient unit "
                    f"{unit_name}; inspect with journalctl -u {unit_name}"
                ),
            )
            result = subprocess.run(
                detached_args,
                cwd=base_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return unit_name, False

            logger.warning(
                "Detached auto-upgrade launch failed; keeping current service running",
                extra={"stdout": (result.stdout or "").strip(), "stderr": (result.stderr or "").strip()},
            )
            _append_auto_upgrade_log(
                base_dir,
                (
                    "Detached auto-upgrade launch failed "
                    f"({_format_detached_failure(result)}); keeping current service running"
                ),
            )
            return None, False
        except Exception as exc:
            logger.warning(
                "Detached auto-upgrade launch failed; keeping current service running",
                exc_info=True,
            )
            _append_auto_upgrade_log(
                base_dir,
                (
                    "Detached auto-upgrade launch failed "
                    f"({_format_detached_failure(exc)}); keeping current service running"
                ),
            )
            return None, False

    subprocess.run(args, cwd=base_dir, check=True)
    return None, True


def _systemctl_command() -> list[str]:
    """Return the base systemctl command, preferring sudo when available."""

    if shutil.which("systemctl") is None:
        return []

    sudo_path = shutil.which("sudo")
    if sudo_path is None:
        return ["systemctl"]

    try:
        sudo_ready = subprocess.run(
            [sudo_path, "-n", "true"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        sudo_ready = None

    if sudo_ready is not None and sudo_ready.returncode == 0:
        return [sudo_path, "-n", "systemctl"]

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


def _restart_service_via_start_script(base_dir: Path, service: str) -> bool:
    """Attempt to restart the managed service using ``start.sh``."""

    start_script = base_dir / "start.sh"
    if not start_script.exists():
        _append_auto_upgrade_log(
            base_dir,
            (
                "start.sh not found after upgrade; manual restart "
                f"required for {service or 'service'}"
            ),
        )
        return False

    try:
        subprocess.run(["./start.sh"], cwd=base_dir, check=True)
    except Exception:  # pragma: no cover - defensive restart handling
        logger.exception("start.sh restart failed after upgrade")
        _append_auto_upgrade_log(
            base_dir,
            (
                f"start.sh restart failed after upgrade for {service or 'service'}; "
                "manual intervention required"
            ),
        )
        return False

    return True


def _record_restart_failure(base_dir: Path, service: str) -> None:
    """Record restart failures and surface a failover alert."""

    _append_auto_upgrade_log(
        base_dir,
        (
            f"Service {service or 'unknown'} failed to restart after upgrade; "
            "manual intervention required"
        ),
    )

    revision = _current_revision(base_dir)
    write_failover_lock(
        base_dir,
        reason=f"Service {service or 'unknown'} failed to restart after upgrade",
        detail=(
            "Restart verification did not succeed; manual intervention required"
        ),
        revision=revision or None,
    )


def _ensure_managed_service(
    base_dir: Path,
    service: str,
    *,
    restart_if_active: bool,
    revert_on_failure: bool,
) -> bool:
    command = _systemctl_command()
    service_is_active = False
    if command and service:
        status_result = subprocess.run(
            [*command, "is-active", "--quiet", service],
            cwd=base_dir,
            check=False,
        )
        service_is_active = status_result.returncode == 0

    def restart_via_systemd(reason: str) -> bool:
        if not command:
            return False
        try:
            subprocess.run(
                [*command, "restart", service],
                cwd=base_dir,
                check=True,
            )
        except subprocess.CalledProcessError:
            logger.exception("systemd restart failed for %s during %s", service, reason)
            return False
        return True

    def handle_restart_failure() -> bool:
        if revert_on_failure:
            _record_restart_failure(base_dir, service)
        return False

    if restart_if_active:
        restarted = False
        if restart_via_systemd("post-upgrade restart"):
            _append_auto_upgrade_log(
                base_dir,
                f"Restarting {service} via systemd restart after upgrade",
            )
            restarted = True
        else:
            _append_auto_upgrade_log(
                base_dir,
                f"Systemd restart unavailable for {service}; restarting via start.sh",
            )
            if not _restart_service_via_start_script(base_dir, service):
                return handle_restart_failure()
            restarted = True

        if not restarted:
            return handle_restart_failure()

        _append_auto_upgrade_log(
            base_dir,
            f"Waiting for {service} to restart after upgrade",
        )
        if not _wait_for_service_restart(base_dir, service):
            _append_auto_upgrade_log(
                base_dir,
                f"Service {service} did not report active status after automatic restart",
            )
            return handle_restart_failure()

        _append_auto_upgrade_log(
            base_dir,
            f"Service {service} restarted successfully after upgrade",
        )
        return True

    if service_is_active:
        return True

    base_message = (
        f"Service {service} not active after upgrade"
        if revert_on_failure
        else f"Service {service} inactive during auto-upgrade check"
    )

    if restart_via_systemd("auto-upgrade recovery"):
        _append_auto_upgrade_log(
            base_dir,
            f"{base_message}; restarting via systemd restart",
        )
    else:
        _append_auto_upgrade_log(
            base_dir,
            f"{base_message}; restarting via start.sh",
        )
        if not _restart_service_via_start_script(base_dir, service):
            _append_auto_upgrade_log(
                base_dir,
                (
                    "Automatic restart via start.sh failed for inactive service "
                    f"{service}"
                ),
            )
            if revert_on_failure:
                _record_restart_failure(base_dir, service)
            return False

    if command:
        _append_auto_upgrade_log(
            base_dir,
            f"Waiting for {service} to restart after upgrade",
        )
        if not _wait_for_service_restart(base_dir, service):
            _append_auto_upgrade_log(
                base_dir,
                (
                    f"Service {service} did not report active status after "
                    "automatic restart"
                ),
            )
            if revert_on_failure:
                _record_restart_failure(base_dir, service)
            return False

    _append_auto_upgrade_log(
        base_dir,
        f"Service {service} restarted successfully during auto-upgrade check",
    )
    return True


def _ensure_development_server(
    base_dir: Path,
    *,
    restart_if_active: bool,
) -> bool:
    if restart_if_active:
        result = subprocess.run(
            ["pkill", "-f", "manage.py runserver"],
            cwd=base_dir,
        )
        if result.returncode == 0:
            _append_auto_upgrade_log(
                base_dir,
                "Restarting development server via start.sh after upgrade",
            )
            start_script = base_dir / "start.sh"
            if start_script.exists():
                try:
                    subprocess.Popen(
                        ["./start.sh"],
                        cwd=base_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except Exception as exc:  # pragma: no cover - subprocess errors
                    _append_auto_upgrade_log(
                        base_dir,
                        (
                            "Failed to restart development server automatically: "
                            f"{exc}"
                        ),
                    )
                    raise
            else:  # pragma: no cover - installation invariant
                _append_auto_upgrade_log(
                    base_dir,
                    "start.sh not found; manual restart required for development server",
                )
        else:
            _append_auto_upgrade_log(
                base_dir,
                (
                    "No manage.py runserver process was active during upgrade; "
                    "skipping development server restart"
                ),
            )
        return True

    check = subprocess.run(
        ["pgrep", "-f", "manage.py runserver"],
        cwd=base_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode == 0:
        return True

    _append_auto_upgrade_log(
        base_dir,
        "Development server inactive during auto-upgrade check; restarting via start.sh",
    )
    start_script = base_dir / "start.sh"
    if not start_script.exists():  # pragma: no cover - installation invariant
        _append_auto_upgrade_log(
            base_dir,
            "start.sh not found; manual restart required for development server",
        )
        return False
    try:
        subprocess.Popen(
            ["./start.sh"],
            cwd=base_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # pragma: no cover - subprocess errors
        _append_auto_upgrade_log(
            base_dir,
            (
                "Failed to restart development server automatically: "
                f"{exc}"
            ),
        )
        return False
    return True


def _ensure_runtime_services(
    base_dir: Path,
    *,
    restart_if_active: bool,
    revert_on_failure: bool,
) -> bool:
    service_file = base_dir / "locks" / "service.lck"
    if service_file.exists():
        try:
            service = service_file.read_text().strip()
        except OSError:
            service = ""
        if not service:
            if restart_if_active:
                _append_auto_upgrade_log(
                    base_dir,
                    "Service restart requested but service lock was empty; "
                    "skipping automatic verification",
                )
            return True
        return _ensure_managed_service(
            base_dir,
            service,
            restart_if_active=restart_if_active,
            revert_on_failure=revert_on_failure,
        )

    return _ensure_development_server(
        base_dir,
        restart_if_active=restart_if_active or revert_on_failure,
    )


def _latest_release() -> tuple[str | None, str | None]:
    """Return the latest release version and revision when available."""

    model = _get_package_release_model()
    if model is None:
        return None, None

    try:
        release = model.latest()
    except DatabaseError:  # pragma: no cover - depends on DB availability
        return None, None
    except Exception:  # pragma: no cover - defensive catch-all
        return None, None

    if not release:
        return None, None

    version = getattr(release, "version", None)
    revision = getattr(release, "revision", None)
    return version, revision


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
    mode = "stable"
    reset_network_failures = True
    try:
        try:
            raw_mode = mode_file.read_text().strip()
        except FileNotFoundError:
            raw_mode = ""
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
            if requested in {"latest", "unstable"}:
                override_mode = "unstable"
            elif requested in {"stable", "normal", "regular"}:
                override_mode = "stable"
        if override_mode:
            mode = override_mode

        mode = {
            "latest": "unstable",
            "version": "stable",
            "normal": "stable",
            "regular": "stable",
        }.get(mode, mode)

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
            fetch_error_output = (exc.stderr or exc.stdout or "").strip()
            error_message = f"Git fetch failed (exit code {exc.returncode})"
            if fetch_error_output:
                error_message = f"{error_message}: {fetch_error_output}"
            _append_auto_upgrade_log(base_dir, error_message)
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
        try:  # pragma: no cover - optional dependency
            from core.notifications import notify  # type: ignore
        except Exception:
            notify = None

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
            _ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
            )
            return

        release_version, release_revision = _latest_release()
        remote_version = release_version or _read_remote_version(base_dir, branch)
        local_version = _read_local_version(base_dir)
        local_revision = _current_revision(base_dir)

        local_timestamp = timezone.localtime(timezone.now())
        upgrade_stamp = local_timestamp.strftime("@ %Y%m%d %H:%M")

        upgrade_was_applied = False

        if mode == "unstable":
            if local_revision == remote_revision and local_revision:
                _ensure_runtime_services(
                    base_dir,
                    restart_if_active=False,
                    revert_on_failure=False,
                )
                return

            if notify:
                notify("Upgrading...", upgrade_stamp)
            args = ["./upgrade.sh", "--latest"]
            upgrade_was_applied = True
        else:
            target_version = remote_version or local_version or "0"

            if local_version == target_version:
                _ensure_runtime_services(
                    base_dir,
                    restart_if_active=False,
                    revert_on_failure=False,
                )
                if startup:
                    startup()
                return

            if release_version and release_revision:
                matches_revision = False
                model = _get_package_release_model()
                if model is None:
                    matches_revision = True
                else:
                    matches_revision = model.matches_revision(
                        release_version, remote_revision
                    )
                if not matches_revision:
                    _append_auto_upgrade_log(
                        base_dir,
                        (
                            "Skipping stable auto-upgrade; release revision does not "
                            "match origin/main"
                        ),
                    )
                    _ensure_runtime_services(
                        base_dir,
                        restart_if_active=False,
                        revert_on_failure=False,
                    )
                    return

            if notify:
                notify("Upgrading...", upgrade_stamp)
            args = ["./upgrade.sh", "--stable"]
            upgrade_was_applied = True

        if upgrade_was_applied:
            args.append("--no-restart")

        with log_file.open("a") as fh:
            fh.write(
                f"{timezone.now().isoformat()} running: {' '.join(args)}\n"
            )

        watcher_unit, ran_inline = _run_upgrade_command(base_dir, args)
        if watcher_unit:
            _append_auto_upgrade_log(
                base_dir,
                (
                    "Auto-upgrade delegated to systemd; review "
                    f"journalctl -u {watcher_unit} for progress"
                ),
            )
            if upgrade_was_applied:
                _append_auto_upgrade_log(
                    base_dir,
                    (
                        "Scheduled post-upgrade health check in %s seconds"
                        % AUTO_UPGRADE_HEALTH_DELAY_SECONDS
                    ),
                )
                _schedule_health_check(1)
            return

        if not ran_inline:
            _append_auto_upgrade_log(
                base_dir,
                (
                    "Inline upgrade skipped because detached launch failed; "
                    "will retry on next cycle"
                ),
            )
            return

        _append_auto_upgrade_log(
            base_dir,
            f"Upgrade script completed successfully: {' '.join(args)}",
        )

        if not _ensure_runtime_services(
            base_dir,
            restart_if_active=True,
            revert_on_failure=True,
        ):
            return

        _append_auto_upgrade_log(
            base_dir,
            "Auto-upgrade verification complete; runtime services are healthy",
        )

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
        logger.warning(
            "Failed to determine revision during auto-upgrade health check failure"
        )

    _add_skipped_revision(base_dir, revision)
    _append_auto_upgrade_log(
        base_dir, "Health check failed; manual intervention required"
    )
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


def execute_scheduled_release(release_id: int) -> None:
    """Run the automated release flow for a scheduled PackageRelease."""

    model = _get_package_release_model()
    if model is None:
        logger.warning("Scheduled release %s skipped: model unavailable", release_id)
        return

    release = model.objects.filter(pk=release_id).first()
    if release is None:
        logger.warning("Scheduled release %s skipped: release not found", release_id)
        return

    try:
        release_workflow.run_headless_publish(release, auto_release=True)
    finally:
        release.clear_schedule(save=True)


@shared_task
def run_scheduled_release(release_id: int) -> None:
    """Entrypoint used by django-celery-beat to trigger scheduled releases."""

    execute_scheduled_release(release_id)


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
            ClientReportSchedule.PERIODICITY_BIMONTHLY,
            ClientReportSchedule.PERIODICITY_QUARTERLY,
            ClientReportSchedule.PERIODICITY_YEARLY,
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
