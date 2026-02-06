from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from apps.core.auto_upgrade import append_auto_upgrade_log


logger = logging.getLogger(__name__)

WATCH_UPGRADE_BINARY = Path("/usr/local/bin/watch-upgrade")


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
                extra={
                    "stdout": (result.stdout or "").strip(),
                    "stderr": (result.stderr or "").strip(),
                },
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
