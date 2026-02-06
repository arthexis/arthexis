from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import psutil

from apps.core.auto_upgrade import append_auto_upgrade_log
from apps.core.systemctl import _systemctl_command


logger = logging.getLogger(__name__)


def _read_process_cmdline(pid: int) -> list[str]:
    """Return the command line for a process when available."""

    try:
        return psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _read_process_start_time(pid: int) -> float | None:
    """Return the process start time in epoch seconds when available."""

    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


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
        append_auto_upgrade_log(
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
        append_auto_upgrade_log(
            base_dir,
            (
                f"start.sh restart failed after upgrade for {service or 'service'}; "
                "manual intervention required"
            ),
        )
        return False

    return True


def _record_restart_failure(base_dir: Path, service: str) -> None:
    """Record restart failures in the auto-upgrade log."""

    append_auto_upgrade_log(
        base_dir,
        (
            f"Service {service or 'unknown'} failed to restart after upgrade; "
            "manual intervention required"
        ),
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
            append_auto_upgrade_log(
                base_dir,
                f"Restarting {service} via systemd restart after upgrade",
            )
            restarted = True
        else:
            append_auto_upgrade_log(
                base_dir,
                f"Systemd restart unavailable for {service}; restarting via start.sh",
            )
            if not _restart_service_via_start_script(base_dir, service):
                return handle_restart_failure()
            restarted = True

        append_auto_upgrade_log(
            base_dir,
            f"Waiting for {service} to restart after upgrade",
        )
        if not _wait_for_service_restart(base_dir, service):
            append_auto_upgrade_log(
                base_dir,
                f"Service {service} did not report active status after automatic restart",
            )
            return handle_restart_failure()

        append_auto_upgrade_log(
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
        append_auto_upgrade_log(
            base_dir,
            f"{base_message}; restarting via systemd restart",
        )
    else:
        append_auto_upgrade_log(
            base_dir,
            f"{base_message}; restarting via start.sh",
        )
        if not _restart_service_via_start_script(base_dir, service):
            append_auto_upgrade_log(
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
        append_auto_upgrade_log(
            base_dir,
            f"Waiting for {service} to restart after upgrade",
        )
        if not _wait_for_service_restart(base_dir, service):
            append_auto_upgrade_log(
                base_dir,
                (
                    f"Service {service} did not report active status after "
                    "automatic restart"
                ),
            )
            if revert_on_failure:
                _record_restart_failure(base_dir, service)
            return False

    append_auto_upgrade_log(
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
            append_auto_upgrade_log(
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
                    append_auto_upgrade_log(
                        base_dir,
                        (
                            "Failed to restart development server automatically: "
                            f"{exc}"
                        ),
                    )
                    raise
            else:  # pragma: no cover - installation invariant
                append_auto_upgrade_log(
                    base_dir,
                    "start.sh not found; manual restart required for development server",
                )
        else:
            append_auto_upgrade_log(
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

    append_auto_upgrade_log(
        base_dir,
        "Development server inactive during auto-upgrade check; restarting via start.sh",
    )
    start_script = base_dir / "start.sh"
    if not start_script.exists():  # pragma: no cover - installation invariant
        append_auto_upgrade_log(
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
        append_auto_upgrade_log(
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
    service_file = base_dir / ".locks" / "service.lck"
    if service_file.exists():
        try:
            service = service_file.read_text().strip()
        except OSError:
            service = ""
        if not service:
            if restart_if_active:
                append_auto_upgrade_log(
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
