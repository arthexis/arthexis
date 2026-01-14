from __future__ import annotations

import contextlib
import logging
import json
import os
import shutil
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
import urllib.error
import urllib.request

import requests

from celery import shared_task
from apps.repos import github
from apps.core.auto_upgrade import (
    AUTO_UPGRADE_FALLBACK_INTERVAL,
    AUTO_UPGRADE_INTERVAL_MINUTES,
    AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES,
    auto_upgrade_fast_lane_enabled,
    DEFAULT_AUTO_UPGRADE_MODE,
    append_auto_upgrade_log,
    auto_upgrade_base_dir,
)
from apps.core.notifications import LcdChannel
from apps.core.systemctl import _systemctl_command
from apps.release import release_workflow
from django.conf import settings
from django.db import DatabaseError, models
from django.utils import timezone
from utils.revision import get_revision


AUTO_UPGRADE_HEALTH_DELAY_SECONDS = 300
AUTO_UPGRADE_SKIP_LOCK_NAME = "auto_upgrade_skip_revisions.lck"
AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME = "auto_upgrade_network_failures.lck"
AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD = 3
AUTO_UPGRADE_FAILURE_LOCK_NAME = "auto_upgrade_failures.lck"
AUTO_UPGRADE_RECENCY_LOCK_NAME = "auto_upgrade_last_run.lck"
STABLE_AUTO_UPGRADE_START = datetime_time(hour=19, minute=30)
STABLE_AUTO_UPGRADE_END = datetime_time(hour=5, minute=30)
WATCH_UPGRADE_BINARY = Path("/usr/local/bin/watch-upgrade")
AUTO_UPGRADE_LCD_CHANNEL_TYPE = LcdChannel.HIGH.value
AUTO_UPGRADE_LCD_CHANNEL_NUM = 1

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
    """Return the :class:`release.models.PackageRelease` model when available."""

    global _PackageReleaseModel

    if _PackageReleaseModel is not None:
        return _PackageReleaseModel

    try:
        from apps.release.models import PackageRelease  # noqa: WPS433 - runtime import
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


def _resolve_release_severity(version: str | None) -> str:
    try:
        return release_workflow.resolve_release_severity(version)
    except Exception:  # pragma: no cover - protective fallback
        logger.exception("Failed to resolve release severity")
        return SEVERITY_NORMAL


@shared_task
def heartbeat() -> None:
    """Log a simple heartbeat message."""
    logger.info("Heartbeat task executed")


@shared_task(bind=True, name="core.tasks.heartbeat")
def legacy_heartbeat(self) -> None:
    """Backward-compatible alias for the heartbeat task.

    Older Celery schedules may still reference ``core.tasks.heartbeat``.
    Register the legacy name so workers avoid "unregistered task" errors
    while routing through the current implementation.
    """

    request = getattr(self, "request", None)
    if request:
        logger.warning(
            "Received legacy heartbeat task; inspect scheduler and broker for stale entries",
            extra={
                "celery_id": getattr(request, "id", None),
                "delivery_info": getattr(request, "delivery_info", None),
                "origin": getattr(request, "hostname", None),
                "headers": getattr(request, "headers", None),
            },
        )

    heartbeat()


SIDE_CAR_LOCK_NAME = "sidecars.lck"


def _sidecar_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / SIDE_CAR_LOCK_NAME


def _load_sidecar_records(base_dir: Path) -> list[dict[str, str]]:
    lock_file = _sidecar_lock_path(base_dir)
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        logger.warning("Unable to read sidecar lock file at %s", lock_file)
        return []

    records: list[dict[str, str]] = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            parts = line.split("    ")  # fallback for legacy space-delimited entries

        if len(parts) < 3:
            continue

        record: dict[str, str] = {"type": parts[0], "name": parts[1], "path": parts[2]}
        if len(parts) > 3 and parts[3]:
            record["service"] = parts[3]
        records.append(record)
    return records


def _read_process_cmdline(pid: int) -> list[str] | None:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw_value = handle.read()
    except OSError:
        return None

    if not raw_value:
        return []

    try:
        decoded = raw_value.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        return None

    return [entry for entry in decoded.split("\0") if entry]


def _read_process_start_time(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            contents = handle.read().split()
    except OSError:
        return None

    try:
        start_ticks = int(contents[21])
    except (IndexError, ValueError):
        return None

    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            uptime_seconds = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None

    try:
        ticks_per_second = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    except (ValueError, AttributeError, KeyError):
        return None

    boot_time = time.time() - uptime_seconds
    return boot_time + (start_ticks / ticks_per_second)


def _migration_pid_matches(pid: int, lock_dir: Path, started_at: float | None) -> bool:
    if pid <= 0:
        return False

    cmdline = _read_process_cmdline(pid)
    if not cmdline:
        return False

    base_dir = lock_dir.parent.resolve()
    expected_script = (base_dir / "scripts" / "migration_server.py").resolve()
    matches_cmd = any(
        part.endswith("migration_server.py") or str(expected_script) in part for part in cmdline
    )
    if not matches_cmd:
        return False

    if started_at is not None:
        process_start = _read_process_start_time(pid)
        if process_start is not None and process_start > started_at + 120:
            return False

    return True


def _is_migration_server_running(lock_dir: Path) -> bool:
    state_path = lock_dir / "migration_server.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except json.JSONDecodeError:
        return True

    pid = payload.get("pid")
    started_at = payload.get("timestamp")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int):
        return False

    if not _migration_pid_matches(pid, lock_dir, started_at if isinstance(started_at, (int, float)) else None):
        return False

    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        try:
            state_path.unlink()
        except OSError:
            pass
        return False
    return True


def _read_service_mode(lock_dir: Path) -> str:
    lock_path = lock_dir / "service_mode.lck"
    try:
        return lock_path.read_text(encoding="utf-8").strip().lower()
    except FileNotFoundError:
        return "embedded"
    except OSError:
        logger.warning("Failed to read service mode from %s", lock_path)
        return "embedded"


def _read_service_name(lock_dir: Path) -> str | None:
    lock_path = lock_dir / "service.lck"
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("Unable to read service name from %s", lock_path)
        return None

    return raw_value or None


def _start_migration_service(base_dir: Path, service_name: str | None, use_systemd: bool) -> None:
    if use_systemd and service_name:
        command = _systemctl_command()
        if command:
            unit_name = service_name if service_name.endswith(".service") else f"{service_name}.service"
            subprocess.run([*command, "restart", unit_name], check=False)
            return

    script = base_dir / "scripts" / "migration-service-start.sh"
    if not script.exists():
        logger.warning("Migration service launcher missing at %s", script)
        return

    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    log_path = base_dir / "logs" / "migration-service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with log_path.open("ab") as log_handle:
            subprocess.Popen(
                ["bash", str(script)],
                cwd=base_dir,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
    except OSError:
        logger.exception("Failed to start migration service for %s", base_dir)


def _ensure_migrator_alive(record: dict[str, str]) -> None:
    base_dir = Path(record.get("path", ""))
    if not base_dir.exists():
        logger.warning("Recorded migrator path missing: %s", base_dir)
        return

    lock_dir = base_dir / ".locks"
    if _is_migration_server_running(lock_dir):
        return

    service_name = record.get("service") or _read_service_name(lock_dir)
    service_mode = _read_service_mode(lock_dir)
    use_systemd = service_mode == "systemd"

    logger.info(
        "Migration service for %s is down; attempting restart via %s",
        base_dir,
        "systemd" if use_systemd else "embedded runner",
    )
    _start_migration_service(base_dir, service_name, use_systemd)


def _ensure_migrators_alive(base_dir: Path) -> None:
    for record in _load_sidecar_records(base_dir):
        if record.get("type") != "migrator":
            continue
        _ensure_migrator_alive(record)


@shared_task
def ensure_migration_service_alive() -> None:
    """Restart migration sidecars when they stop running."""

    base_dir = _project_base_dir()
    _ensure_migrators_alive(base_dir)


def _project_base_dir() -> Path:
    """Return the filesystem base directory for runtime operations."""

    return auto_upgrade_base_dir()

def _recency_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_RECENCY_LOCK_NAME


def _record_auto_upgrade_timestamp(base_dir: Path) -> None:
    lock_path = _recency_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(timezone.now().isoformat(), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade recency lockfile")


def _auto_upgrade_ran_recently(base_dir: Path, interval_minutes: int) -> bool:
    lock_path = _recency_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Failed to read auto-upgrade recency lockfile")
        return False

    if not raw_value:
        return False

    try:
        recorded_time = datetime.fromisoformat(raw_value)
    except ValueError:
        logger.warning(
            "Invalid auto-upgrade recency lockfile contents: %s", raw_value
        )
        return False

    if timezone.is_naive(recorded_time):
        recorded_time = timezone.make_aware(recorded_time)

    now = timezone.now()
    if recorded_time > now:
        logger.warning(
            "Auto-upgrade recency lockfile is in the future; ignoring timestamp"
        )
        return False

    return recorded_time > now - timedelta(minutes=interval_minutes)


def _resolve_auto_upgrade_interval_minutes(mode: str) -> int:
    base_dir = auto_upgrade_base_dir()
    if auto_upgrade_fast_lane_enabled(base_dir):
        return AUTO_UPGRADE_FAST_LANE_INTERVAL_MINUTES

    interval_minutes = AUTO_UPGRADE_INTERVAL_MINUTES.get(
        mode, AUTO_UPGRADE_FALLBACK_INTERVAL
    )

    override_interval = os.environ.get("ARTHEXIS_UPGRADE_FREQ")
    if override_interval:
        try:
            parsed_interval = int(override_interval)
        except ValueError:
            parsed_interval = None
        else:
            if parsed_interval > 0:
                interval_minutes = parsed_interval

    return interval_minutes


def _normalize_channel_mode(value: str | None) -> str | None:
    """Return a lowercased, whitespace-trimmed channel value."""

    if value is None:
        return None

    normalized = value.strip().lower()
    return normalized or None


def _upgrade_command_args(mode: str) -> list[str]:
    """Return the platform-appropriate upgrade command for ``mode``."""

    script = "./upgrade.sh"
    if os.name == "nt" or sys.platform == "win32":
        script = "upgrade.bat"
    return [script, f"--{mode}"]


def _detect_path_owner(base_dir: Path) -> tuple[str | None, str | None]:
    """Return the owning username and home directory for ``base_dir``."""

    if sys.platform == "win32":
        return None, None

    import pwd  # noqa: WPS433 - platform-specific import

    try:
        stat_info = base_dir.stat()
        user = pwd.getpwuid(stat_info.st_uid)
    except (OSError, KeyError):
        return None, None

    return user.pw_name, user.pw_dir


def _run_upgrade_command(
    base_dir: Path, args: list[str], *, require_detached: bool = False
) -> tuple[str | None, bool]:
    """Run the upgrade script, detaching from system services when possible.

    Returns a tuple of ``(unit_name, ran_inline)`` where ``unit_name`` is set
    when the upgrade was delegated to a transient systemd unit and
    ``ran_inline`` indicates whether the upgrade script was executed inline.
    When ``require_detached`` is ``True`` the command will only execute when
    the transient systemd unit launch succeeds; otherwise the function will
    return without running the upgrade inline.
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
        lock_path = base_dir / ".locks" / "service.lck"
        try:
            value = lock_path.read_text().strip()
        except OSError:
            return ""
        return value

    systemd_run_command = _systemd_run_command()
    running_in_service = bool(os.environ.get("INVOCATION_ID"))
    service_name = _read_service_name()
    path_owner, path_home = _detect_path_owner(base_dir)

    missing_prereqs: list[str] = []
    if require_detached and not running_in_service:
        missing_prereqs.append("systemd context unavailable")
    if not service_name:
        missing_prereqs.append("service name unknown")
    if not systemd_run_command:
        missing_prereqs.append("systemd-run missing")
    if not WATCH_UPGRADE_BINARY.exists():
        missing_prereqs.append("watch-upgrade helper missing (run ./env-refresh.sh)")

    if require_detached and missing_prereqs:
        reason = "; ".join(missing_prereqs)
        append_auto_upgrade_log(
            base_dir,
            (
                "Detached auto-upgrade unavailable; "
                f"skipping inline execution ({reason})"
            ),
        )
        return None, False

    if (
        systemd_run_command
        and service_name
        and WATCH_UPGRADE_BINARY.exists()
        and (running_in_service or require_detached)
    ):
        unit_name = f"upgrade-watcher-{uuid.uuid4().hex}"
        detached_args = [
            *systemd_run_command,
            "--unit",
            unit_name,
            "--description",
            f"Watch {service_name} upgrade",
        ]

        if path_owner:
            detached_args.extend(["--uid", path_owner])
            if path_home:
                detached_args.extend(["--setenv", f"HOME={path_home}"])

        detached_args.extend(
            [
            "--setenv",
            f"ARTHEXIS_BASE_DIR={base_dir}",
            str(WATCH_UPGRADE_BINARY),
            service_name,
            *args,
            ]
        )

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
            append_auto_upgrade_log(
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
            append_auto_upgrade_log(
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
            append_auto_upgrade_log(
                base_dir,
                (
                    "Detached auto-upgrade launch failed "
                    f"({_format_detached_failure(exc)}); keeping current service running"
                ),
            )
            return None, False

    command = args
    run_kwargs: dict[str, object] = {}

    if os.name == "nt":
        run_kwargs["shell"] = True

    try:
        subprocess.run(command, cwd=base_dir, check=True, **run_kwargs)
    except OSError as exc:  # pragma: no cover - platform-specific
        logger.warning(
            "Inline auto-upgrade launch failed; will retry on next cycle",
            exc_info=True,
        )
        append_auto_upgrade_log(
            base_dir,
            (
                "Inline auto-upgrade launch failed "
                f"({exc}); will retry on next cycle"
            ),
        )
        return None, False

    return None, True


def _delegate_upgrade_via_script(base_dir: Path, args: list[str]) -> str | None:
    """Launch the delegated upgrade helper script and return the unit name."""

    script = base_dir / "scripts" / "delegated-upgrade.sh"
    if not script.exists():
        append_auto_upgrade_log(
            base_dir,
            "Delegated upgrade script missing; skipping auto-upgrade delegation",
        )
        return None

    if not WATCH_UPGRADE_BINARY.exists():
        append_auto_upgrade_log(
            base_dir,
            "watch-upgrade helper missing; skipping delegated auto-upgrade",
        )
        return None

    command = [str(script), *args]
    result = subprocess.run(
        command,
        cwd=base_dir,
        check=False,
        capture_output=True,
        text=True,
    )

    unit_name = "delegated-upgrade"
    for line in (result.stdout or "").splitlines():
        if line.startswith("UNIT_NAME="):
            unit_name = line.split("=", 1)[1].strip() or unit_name
            break

    if result.returncode != 0:
        stderr_output = (result.stderr or result.stdout or "").strip()
        details = f"; {stderr_output}" if stderr_output else ""
        append_auto_upgrade_log(
            base_dir,
            (
                "Delegated upgrade launch failed "
                f"(exit code {result.returncode}{details})"
            ),
        )
        return None

    return unit_name


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

        if not restarted:
            return handle_restart_failure()

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


def _parse_github_slug(remote_url: str) -> str | None:
    """Normalize the GitHub repository slug from the ``origin`` remote URL."""

    cleaned = remote_url.strip()
    if not cleaned:
        return None

    if "github.com" not in cleaned:
        return None

    if cleaned.startswith("git@github.com:"):
        slug = cleaned.split(":", 1)[-1]
    else:
        parts = cleaned.split("github.com/", 1)
        slug = parts[-1] if len(parts) > 1 else ""

    if slug.endswith(".git"):
        slug = slug[:-4]

    return slug or None


def _resolve_github_slug(base_dir: Path) -> str | None:
    """Return the ``owner/repo`` slug for the ``origin`` remote when available."""

    try:
        remote_url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=base_dir,
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None

    return _parse_github_slug(remote_url)


def _fetch_ci_workflow_status(repo_slug: str, branch: str, workflow: str = "ci.yml") -> str | None:
    """Return the latest completed CI workflow status for ``branch`` when available."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arthexis-auto-upgrade",
    }

    token = os.environ.get("GITHUB_TOKEN", "")
    if isinstance(token, str):
        cleaned = token.strip()
        if cleaned:
            headers["Authorization"] = f"token {cleaned}"

    url = f"https://api.github.com/repos/{repo_slug}/actions/workflows/{workflow}/runs"

    response = None
    try:
        response = requests.get(
            url,
            headers=headers,
            params={"branch": branch, "status": "completed", "per_page": 1},
            timeout=10,
        )
    except requests.RequestException:
        logger.warning("Failed to query CI workflow status for %s", repo_slug, exc_info=True)
        return None

    try:
        if response is None or response.status_code != 200:
            logger.warning(
                "CI workflow status request for %s returned %s",
                repo_slug,
                getattr(response, "status_code", "<unknown>"),
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        runs = payload.get("workflow_runs")
        if not isinstance(runs, list) or not runs:
            return None

        conclusion = runs[0].get("conclusion")
        return conclusion.lower() if isinstance(conclusion, str) else None
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()


def _fetch_ci_status(repo_slug: str, revision: str) -> str | None:
    """Return the combined CI status for ``revision`` when available."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arthexis-auto-upgrade",
    }

    token = os.environ.get("GITHUB_TOKEN", "")
    if isinstance(token, str):
        cleaned = token.strip()
        if cleaned:
            headers["Authorization"] = f"token {cleaned}"

    url = f"https://api.github.com/repos/{repo_slug}/commits/{revision}/status"

    response = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException:
        logger.warning("Failed to query CI status for %s", repo_slug, exc_info=True)
        return None

    try:
        if response is None or response.status_code != 200:
            logger.warning(
                "CI status request for %s returned %s", repo_slug, getattr(response, "status_code", "<unknown>")
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        state = payload.get("state")
        return state.lower() if isinstance(state, str) else None
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()


def _ci_status_for_revision(base_dir: Path, revision: str, branch: str = "main") -> str | None:
    """Return the CI status value aligned with the main branch badge when available."""

    repo_slug = _resolve_github_slug(base_dir)
    if not repo_slug:
        return None

    branch_status = _fetch_ci_workflow_status(repo_slug, branch or "main")
    if branch_status:
        return branch_status

    return _fetch_ci_status(repo_slug, revision)


def _is_within_stable_upgrade_window(current: datetime | None = None) -> bool:
    """Return whether the current time is inside the stable upgrade window."""

    if current is None:
        current = timezone.localtime(timezone.now())
    else:
        current = timezone.localtime(current)

    current_time = current.time()
    return (
        current_time >= STABLE_AUTO_UPGRADE_START
        or current_time <= STABLE_AUTO_UPGRADE_END
    )


def _skip_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_SKIP_LOCK_NAME


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
        append_auto_upgrade_log(
            base_dir, f"Recorded blocked revision {revision} for auto-upgrade"
        )
    except OSError:
        logger.warning(
            "Failed to update auto-upgrade skip lockfile with revision %s", revision
        )


def _network_failure_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_NETWORK_FAILURE_LOCK_NAME


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
    append_auto_upgrade_log(
        base_dir,
        f"Auto-upgrade network failure {count}: {detail}",
    )
    return count


def _charge_point_active(base_dir: Path) -> bool:
    lock_path = base_dir / ".locks" / "charging.lck"
    if lock_path.exists():
        return True
    try:
        from apps.ocpp import store  # type: ignore
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
        append_auto_upgrade_log(
            base_dir,
            "Skipping reboot after repeated auto-upgrade network failures; a charge point is active",
        )
        return
    append_auto_upgrade_log(
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


def _auto_upgrade_failure_lock_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / AUTO_UPGRADE_FAILURE_LOCK_NAME


def _read_auto_upgrade_failure_count(base_dir: Path) -> int:
    lock_path = _auto_upgrade_failure_lock_path(base_dir)
    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return 0
    except OSError:
        logger.warning("Failed to read auto-upgrade failure lockfile")
        return 0
    if not raw_value:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid auto-upgrade failure lockfile contents: %s", raw_value
        )
        return 0


def _write_auto_upgrade_failure_count(base_dir: Path, count: int) -> None:
    lock_path = _auto_upgrade_failure_lock_path(base_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(count), encoding="utf-8")
    except OSError:
        logger.warning("Failed to update auto-upgrade failure lockfile")


def _reset_auto_upgrade_failure_count(base_dir: Path) -> None:
    lock_path = _auto_upgrade_failure_lock_path(base_dir)
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        logger.warning("Failed to remove auto-upgrade failure lockfile")


def _normalize_failure_reason(reason: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", reason.upper())
    if not tokens:
        return "AUTO-UPGRADE-FAIL"
    return "-".join(tokens[:5])


def _short_revision(revision: str | None) -> str:
    if not revision:
        return "-"
    trimmed = str(revision)
    return trimmed[-6:] if len(trimmed) > 6 else trimmed


def _resolve_upgrade_subject() -> str:
    from apps.nodes.models import Node

    fallback_name = socket.gethostname() or "node"

    try:
        node = Node.get_local()
    except Exception:
        logger.warning(
            "Auto-upgrade notification node lookup failed", exc_info=True
        )
        node_name = fallback_name
    else:
        node_name = getattr(node, "hostname", None) or fallback_name

    return f"Upgrade {node_name}".strip()


def _broadcast_upgrade_start_message(
    local_revision: str | None, remote_revision: str | None
) -> None:
    from apps.nodes.models import NetMessage

    subject = _resolve_upgrade_subject()
    previous_revision = _short_revision(local_revision)
    next_revision = _short_revision(remote_revision)
    body = f"{previous_revision} - {next_revision}"

    try:
        NetMessage.broadcast(
            subject=subject,
            body=body,
            lcd_channel_type=AUTO_UPGRADE_LCD_CHANNEL_TYPE,
            lcd_channel_num=AUTO_UPGRADE_LCD_CHANNEL_NUM,
        )
    except Exception:
        logger.warning(
            "Failed to broadcast auto-upgrade start Net Message", exc_info=True
        )


def _send_auto_upgrade_failure_message(base_dir: Path, reason: str, failure_count: int) -> None:
    from apps.nodes.models import NetMessage, Node

    try:
        node = Node.get_local()
    except Exception:
        logger.warning(
            "Auto-upgrade failure Net Message skipped: local node unavailable",
            exc_info=True,
        )
        return

    node_name = getattr(node, "hostname", None) or socket.gethostname() or "node"
    timestamp = timezone.localtime(timezone.now())
    formatted_time = timestamp.strftime("%H:%M")
    subject = f"{node_name} {formatted_time}"
    body = f"{reason} x{failure_count}"

    try:
        NetMessage.broadcast(
            subject=subject,
            body=body,
            lcd_channel_type=AUTO_UPGRADE_LCD_CHANNEL_TYPE,
            lcd_channel_num=AUTO_UPGRADE_LCD_CHANNEL_NUM,
        )
    except Exception:
        logger.warning(
            "Failed to broadcast auto-upgrade failure Net Message", exc_info=True
        )


def _resolve_auto_upgrade_change_tag(
    initial_version: str | None,
    current_version: str | None,
    initial_revision: str,
    current_revision: str,
) -> str:
    if initial_version != current_version:
        return current_version or "-"
    if initial_revision != current_revision:
        return _short_revision(current_revision)
    return "CLEAN"


def _send_auto_upgrade_check_message(status: str, change_tag: str) -> None:
    from apps.nodes.models import NetMessage

    timestamp = timezone.localtime(timezone.now()).strftime("%H:%M")
    subject = f"UP-CHECK {timestamp}"

    try:
        NetMessage.broadcast(
            subject=subject,
            body=f"{status[:16]} {change_tag}",
            lcd_channel_type=AUTO_UPGRADE_LCD_CHANNEL_TYPE,
            lcd_channel_num=AUTO_UPGRADE_LCD_CHANNEL_NUM,
        )
    except Exception:
        logger.warning(
            "Failed to broadcast auto-upgrade check Net Message", exc_info=True
        )


def _record_auto_upgrade_failure(base_dir: Path, reason: str) -> int:
    normalized_reason = _normalize_failure_reason(reason)
    count = _read_auto_upgrade_failure_count(base_dir) + 1
    _write_auto_upgrade_failure_count(base_dir, count)
    append_auto_upgrade_log(
        base_dir,
        f"Auto-upgrade failure {count}: {normalized_reason}",
    )
    _send_auto_upgrade_failure_message(base_dir, normalized_reason, count)
    return count


def _classify_auto_upgrade_failure(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        if _is_network_failure(exc):
            return "NETWORK"
        command = exc.cmd
        if isinstance(command, (list, tuple)):
            command_text = " ".join(str(item) for item in command)
        else:
            command_text = str(command)
        if "git" in command_text:
            return "GIT-ERROR"
        if "upgrade.sh" in command_text:
            return "UPGRADE-SCRIPT"
        return "SUBPROCESS"
    return exc.__class__.__name__


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


@dataclass
class AutoUpgradeOperations:
    git_fetch: Callable[[Path, str], None]
    resolve_remote_revision: Callable[[Path, str], str]
    ensure_runtime_services: Callable[[Path, bool, bool], None]
    delegate_upgrade: Callable[[Path, list[str]], str | None]
    run_upgrade_command: Callable[[Path, list[str]], tuple[str | None, bool]]


@dataclass
class AutoUpgradeMode:
    mode: str
    admin_override: bool
    override_log: str | None
    mode_file_exists: bool
    mode_file_physical: bool
    interval_minutes: int


@dataclass
class AutoUpgradeState:
    reset_network_failures: bool = True
    failure_recorded: bool = False


@dataclass
class AutoUpgradeRepositoryState:
    remote_revision: str
    release_version: str | None
    release_revision: str | None
    remote_version: str | None
    local_version: str | None
    local_revision: str
    severity: str


def _default_auto_upgrade_operations() -> AutoUpgradeOperations:
    return AutoUpgradeOperations(
        git_fetch=_git_fetch,
        resolve_remote_revision=_git_remote_revision,
        ensure_runtime_services=_ensure_runtime_services,
        delegate_upgrade=_delegate_upgrade_via_script,
        run_upgrade_command=_run_upgrade_command,
    )


def _git_fetch(base_dir: Path, branch: str) -> None:
    subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=base_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_remote_revision(base_dir: Path, branch: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", f"origin/{branch}"],
        cwd=base_dir,
        stderr=subprocess.STDOUT,
        text=True,
    ).strip()


def _resolve_auto_upgrade_mode(
    base_dir: Path, channel_override: str | None
) -> AutoUpgradeMode:
    mode_file = base_dir / ".locks" / "auto_upgrade.lck"
    mode_file_exists = mode_file.exists()
    mode = DEFAULT_AUTO_UPGRADE_MODE
    mode_file_physical = mode_file.is_file()

    try:
        raw_mode = mode_file.read_text() if mode_file_exists else ""
    except (OSError, UnicodeDecodeError):
        logger.warning("Failed to read auto-upgrade mode lockfile", exc_info=True)
    else:
        cleaned_mode = _normalize_channel_mode(raw_mode)
        if cleaned_mode:
            mode = cleaned_mode

    override_log: str | None = None
    override_mode = _normalize_channel_mode(channel_override)
    if override_mode:
        if override_mode in {"latest", "unstable"}:
            mode = "unstable"
            override_log = "latest"
        elif override_mode in {"stable", "normal", "regular", "version"}:
            mode = "stable"
            if override_mode == "stable":
                override_log = "stable"

    mode = {
        "latest": "unstable",
        "unstable": "unstable",
        "version": "stable",
        "stable": "stable",
        "normal": "stable",
        "regular": "stable",
    }.get(mode, mode)

    interval_minutes = _resolve_auto_upgrade_interval_minutes(mode)

    return AutoUpgradeMode(
        mode=mode,
        admin_override=channel_override is not None,
        override_log=override_log,
        mode_file_exists=mode_file_exists,
        mode_file_physical=mode_file_physical,
        interval_minutes=interval_minutes,
    )


def _log_auto_upgrade_trigger(base_dir: Path) -> Path:
    return append_auto_upgrade_log(base_dir, "check_github_updates triggered")


def _apply_stable_schedule_guard(
    base_dir: Path, mode: AutoUpgradeMode, ops: AutoUpgradeOperations
) -> bool:
    if mode.mode != "stable" or mode.admin_override or not mode.mode_file_exists:
        return True

    now_local = timezone.localtime(timezone.now())
    if _is_within_stable_upgrade_window(now_local):
        return True

    append_auto_upgrade_log(
        base_dir,
        "Skipping stable auto-upgrade; outside the 7:30 PM to 5:30 AM window",
    )
    ops.ensure_runtime_services(
        base_dir,
        restart_if_active=False,
        revert_on_failure=False,
    )
    return False


def _fetch_repository_state(
    base_dir: Path,
    branch: str,
    mode: AutoUpgradeMode,
    ops: AutoUpgradeOperations,
    state: AutoUpgradeState,
) -> AutoUpgradeRepositoryState | None:
    try:
        ops.git_fetch(base_dir, branch)
    except subprocess.CalledProcessError as exc:
        fetch_error_output = (exc.stderr or exc.stdout or "").strip()
        error_message = f"Git fetch failed (exit code {exc.returncode})"
        if fetch_error_output:
            error_message = f"{error_message}: {fetch_error_output}"
        append_auto_upgrade_log(base_dir, error_message)
        handled_network = _handle_network_failure_if_applicable(base_dir, exc)
        if handled_network:
            state.reset_network_failures = False
            _record_auto_upgrade_failure(base_dir, _classify_auto_upgrade_failure(exc))
            state.failure_recorded = True
        else:
            state.failure_recorded = True
        raise

    try:
        remote_revision = ops.resolve_remote_revision(base_dir, branch)
    except subprocess.CalledProcessError as exc:
        if _handle_network_failure_if_applicable(base_dir, exc):
            state.reset_network_failures = False
        raise

    skipped_revisions = _load_skipped_revisions(base_dir)
    if remote_revision in skipped_revisions:
        append_auto_upgrade_log(
            base_dir,
            f"Skipping auto-upgrade for blocked revision {remote_revision}",
        )
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
        )
        return None

    ci_status = _ci_status_for_revision(base_dir, remote_revision, branch=branch)
    if ci_status and ci_status != "success":
        append_auto_upgrade_log(
            base_dir,
            (
                "Skipping auto-upgrade; CI status is "
                f"{ci_status} for revision {remote_revision}"
            ),
        )
        _record_auto_upgrade_failure(base_dir, "CI-FAILING")
        state.failure_recorded = True
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
        )
        return None

    release_version, release_revision = _latest_release()
    remote_version = release_version or _read_remote_version(base_dir, branch)
    local_version = _read_local_version(base_dir)
    severity = _resolve_release_severity(remote_version)
    local_revision = _current_revision(base_dir)

    return AutoUpgradeRepositoryState(
        remote_revision=remote_revision,
        release_version=release_version,
        release_revision=release_revision,
        remote_version=remote_version,
        local_version=local_version,
        local_revision=local_revision,
        severity=severity,
    )


def _plan_auto_upgrade(
    base_dir: Path,
    mode: AutoUpgradeMode,
    repo_state: AutoUpgradeRepositoryState,
    notify: Callable[[str, str], Any] | None,
    startup: Callable[[], Any] | None,
    ops: AutoUpgradeOperations,
) -> tuple[list[str], bool] | None:
    upgrade_was_applied = False
    args: list[str] = []
    upgrade_subject = _resolve_upgrade_subject()
    upgrade_stamp = timezone.localtime(timezone.now()).strftime("@ %Y%m%d %H:%M")

    if mode.mode == "unstable":
        if repo_state.severity == SEVERITY_LOW:
            append_auto_upgrade_log(
                base_dir,
                "Skipping auto-upgrade for low severity patch on latest channel",
            )
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
            )
            if startup:
                startup()
            return None

        if (
            repo_state.local_revision == repo_state.remote_revision
            and repo_state.local_revision
        ):
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
            )
            return None

        if notify:
            notify(upgrade_subject, upgrade_stamp)
        args = _upgrade_command_args("latest")
        upgrade_was_applied = True
    else:
        target_version = repo_state.remote_version or repo_state.local_version or "0"

        if repo_state.local_version == target_version:
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
            )
            if startup:
                startup()
            return None

        if repo_state.release_version and repo_state.release_revision:
            matches_revision = False
            model = _get_package_release_model()
            if model is None:
                matches_revision = True
            else:
                matches_revision = model.matches_revision(
                    repo_state.release_version, repo_state.remote_revision
                )
            if not matches_revision:
                append_auto_upgrade_log(
                    base_dir,
                    (
                        "Skipping stable auto-upgrade; release revision does not "
                        "match origin/main"
                    ),
                )
                ops.ensure_runtime_services(
                    base_dir,
                    restart_if_active=False,
                    revert_on_failure=False,
                )
                return None

        if notify:
            notify(upgrade_subject, upgrade_stamp)
        args = _upgrade_command_args("stable")
        upgrade_was_applied = True

    if os.name != "nt" and args and args[0].lower().endswith(".bat"):
        args = ["./upgrade.sh", *args[1:]]
        append_auto_upgrade_log(
            base_dir,
            "Normalized upgrade command for POSIX host",
        )

    return args, upgrade_was_applied


def _execute_upgrade_plan(
    base_dir: Path,
    mode: AutoUpgradeMode,
    repo_state: AutoUpgradeRepositoryState,
    args: list[str],
    upgrade_was_applied: bool,
    log_file: Path,
    ops: AutoUpgradeOperations,
    state: AutoUpgradeState,
):
    if (
        upgrade_was_applied
        and mode.mode == "stable"
        and not mode.admin_override
        and mode.mode_file_physical
    ):
        if _auto_upgrade_ran_recently(base_dir, mode.interval_minutes):
            append_auto_upgrade_log(
                base_dir,
                (
                    "Skipping auto-upgrade; last run was less than "
                    f"{mode.interval_minutes} minutes ago"
                ),
            )
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
            )
            return

    with log_file.open("a") as fh:
        fh.write(f"{timezone.now().isoformat()} running: {' '.join(args)}\n")

    if (
        upgrade_was_applied
        and mode.mode == "unstable"
        and not mode.admin_override
        and mode.mode_file_physical
        and _auto_upgrade_ran_recently(base_dir, mode.interval_minutes)
    ):
        append_auto_upgrade_log(
            base_dir,
            (
                "Skipping auto-upgrade; last run was less than "
                f"{mode.interval_minutes} minutes ago"
            ),
        )
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
        )
        return

    if upgrade_was_applied:
        _broadcast_upgrade_start_message(
            repo_state.local_revision, repo_state.remote_revision
        )
        _record_auto_upgrade_timestamp(base_dir)

    delegated_unit: str | None = None
    if ops.delegate_upgrade.__module__ != __name__:
        delegated_unit = ops.delegate_upgrade(base_dir, args)

    if delegated_unit:
        append_auto_upgrade_log(
            base_dir,
            (
                "Auto-upgrade delegated to systemd; review "
                f"journalctl -u {delegated_unit} for progress"
            ),
        )
        if upgrade_was_applied:
            append_auto_upgrade_log(
                base_dir,
                (
                    "Scheduled post-upgrade health check in %s seconds"
                    % AUTO_UPGRADE_HEALTH_DELAY_SECONDS
                ),
            )
            _schedule_health_check(1)
        return

    delegated_unit, ran_inline = ops.run_upgrade_command(base_dir, args)

    if delegated_unit:
        append_auto_upgrade_log(
            base_dir,
            (
                "Auto-upgrade delegated to systemd; review "
                f"journalctl -u {delegated_unit} for progress"
            ),
        )
        if upgrade_was_applied:
            append_auto_upgrade_log(
                base_dir,
                (
                    "Scheduled post-upgrade health check in %s seconds"
                    % AUTO_UPGRADE_HEALTH_DELAY_SECONDS
                ),
            )
            _schedule_health_check(1)
        return

    if not ran_inline:
        append_auto_upgrade_log(
            base_dir,
            "Delegated auto-upgrade launch failed; will retry on next cycle",
        )
        _record_auto_upgrade_failure(base_dir, "UPGRADE-LAUNCH")
        state.failure_recorded = True
        return

    ops.ensure_runtime_services(
        base_dir,
        restart_if_active=True,
        revert_on_failure=True,
    )


def _handle_auto_upgrade_failure(
    base_dir: Path, exc: Exception, state: AutoUpgradeState
) -> None:
    if not state.failure_recorded:
        state.failure_recorded = True
        _record_auto_upgrade_failure(base_dir, _classify_auto_upgrade_failure(exc))


def _finalize_auto_upgrade(base_dir: Path, state: AutoUpgradeState) -> None:
    if state.reset_network_failures and not state.failure_recorded:
        _reset_network_failure_count(base_dir)
    if not state.failure_recorded:
        _reset_auto_upgrade_failure_count(base_dir)


@shared_task
def check_github_updates(
    channel_override: str | None = None,
    *,
    operations: AutoUpgradeOperations | None = None,
) -> None:
    """Check the GitHub repo for updates and upgrade if needed."""

    base_dir = _project_base_dir()
    branch = "main"
    ops = operations or _default_auto_upgrade_operations()
    state = AutoUpgradeState()
    status = "FAILED"
    initial_version = _read_local_version(base_dir)
    initial_revision = _current_revision(base_dir)

    try:
        mode = _resolve_auto_upgrade_mode(base_dir, channel_override)
        status = "NO-UPDATES"

        if not _apply_stable_schedule_guard(base_dir, mode, ops):
            status = "SKIPPED"
            return

        startup = None
        try:
            from apps.nodes.apps import _startup_notification as startup  # type: ignore
        except Exception:
            startup = None

        log_file = _log_auto_upgrade_trigger(base_dir)

        if mode.override_log:
            append_auto_upgrade_log(
                base_dir,
                f"Using admin override channel: {mode.override_log}",
            )

        notify = None
        try:  # pragma: no cover - optional dependency
            from apps.core.notifications import notify  # type: ignore
        except Exception:
            notify = None

        repo_state = _fetch_repository_state(base_dir, branch, mode, ops, state)
        if repo_state is None:
            status = "SKIPPED"
            return

        plan = _plan_auto_upgrade(base_dir, mode, repo_state, notify, startup, ops)
        if plan is None:
            status = "NO-UPDATES"
            return

        args, upgrade_was_applied = plan

        status = "APPLIED" if upgrade_was_applied else "NO-UPDATES"

        _execute_upgrade_plan(
            base_dir,
            mode,
            repo_state,
            args,
            upgrade_was_applied,
            log_file,
            ops,
            state,
        )
    except Exception as exc:
        status = "FAILED"
        _handle_auto_upgrade_failure(base_dir, exc, state)
        raise
    finally:
        current_version = _read_local_version(base_dir)
        current_revision = _current_revision(base_dir)
        change_tag = _resolve_auto_upgrade_change_tag(
            initial_version, current_version, initial_revision, current_revision
        )
        _finalize_auto_upgrade(base_dir, state)
        _send_auto_upgrade_check_message(status, change_tag)


@shared_task
def poll_emails() -> None:
    """Poll all configured email collectors for new messages."""
    try:
        from apps.emails.models import EmailCollector
    except Exception:  # pragma: no cover - app not ready
        return

    for collector in EmailCollector.objects.all():
        collector.collect()


def _record_health_check_result(
    base_dir: Path, attempt: int, status: int | None, detail: str
) -> None:
    status_display = status if status is not None else "unreachable"
    message = "Health check attempt %s %s (%s)" % (attempt, detail, status_display)
    append_auto_upgrade_log(base_dir, message)


def _schedule_health_check(next_attempt: int) -> None:
    verify_auto_upgrade_health.apply_async(
        kwargs={"attempt": next_attempt},
        countdown=AUTO_UPGRADE_HEALTH_DELAY_SECONDS,
    )


def _current_revision(base_dir: Path) -> str:
    """Return the current git revision when available."""

    del base_dir  # Base directory handled by shared revision helper.

    try:
        return get_revision()
    except Exception:  # pragma: no cover - defensive fallback
        logger.warning(
            "Failed to resolve git revision for auto-upgrade logging", exc_info=True
        )
        return ""


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
