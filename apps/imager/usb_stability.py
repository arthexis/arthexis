"""USB stability helpers for destructive SD-card writes."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

QUIET_USB_ENV = "IMAGER_QUIET_USB_DURING_WRITE"
QUIET_USB_LOCK = Path.home() / ".cache" / "arthexis" / "quiet-usb.lock"
BASTION_USB_REFRESH_HOLD = Path("/run/bastion-ssh/refresh.disabled")
BASTION_USB_REFRESH_HOLD_TOKENS = Path("/run/bastion-ssh/refresh.disabled.d")

QUIET_SYSTEM_UNITS: tuple[str, ...] = (
    "arthexis-usb-inventory.service",
    "arthexis-usb-inventory.timer",
    "bastion-usb-refresh.timer",
    "bastion-usb-refresh.service",
    "kindle-postbox.service",
    "udisks2.service",
)
QUIET_USER_UNITS: tuple[str, ...] = (
    "gvfs-udisks2-volume-monitor.service",
    "gvfs-mtp-volume-monitor.service",
    "gvfs-gphoto2-volume-monitor.service",
    "gvfs-afc-volume-monitor.service",
)
ACTIVE_SYSTEMD_STATES = {"active", "activating", "reloading"}


Runner = Callable[..., subprocess.CompletedProcess[str]]
Logger = Callable[[str], None]
_QUIET_USB_THREAD_LOCK = threading.Lock()


@dataclass
class QuietUsbSession:
    """State captured while local USB pollers are paused."""

    enabled: bool
    system_units: list[tuple[str, str]] = field(default_factory=list)
    user_units: list[tuple[str, str]] = field(default_factory=list)
    bastion_hold_prior_state: str = ""
    bastion_hold_token_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def quiet_usb_enabled() -> bool:
    """Return whether destructive writes should pause USB pollers by default."""

    raw_value = os.environ.get(QUIET_USB_ENV, "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off", "disable", "disabled"}


def _log(log: Logger | None, message: str) -> None:
    if log is not None:
        log(message)


def _run(
    runner: Runner,
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _command_available(command: str, *, runner: Runner) -> bool:
    del runner
    return shutil.which(command) is not None


def _sudo_prefix(*, runner: Runner) -> list[str] | None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if not _command_available("sudo", runner=runner):
        return None
    result = _run(runner, ["sudo", "-n", "true"])
    if result.returncode != 0:
        return None
    return ["sudo", "-n"]


def _systemctl_available(*, runner: Runner) -> bool:
    return _command_available("systemctl", runner=runner)


def _system_unit_state(unit: str, *, runner: Runner) -> str:
    result = _run(runner, ["systemctl", "is-active", unit])
    state = result.stdout.strip() or result.stderr.strip()
    return state or "unknown"


def _user_unit_state(unit: str, *, runner: Runner) -> str:
    result = _run(runner, ["systemctl", "--user", "is-active", unit])
    state = result.stdout.strip() or result.stderr.strip()
    return state or "unknown"


def _record_warning(session: QuietUsbSession, log: Logger | None, message: str) -> None:
    session.warnings.append(message)
    _log(log, f"quiet-usb warning: {message}")


def _pause_system_units(
    session: QuietUsbSession,
    *,
    runner: Runner,
    log: Logger | None,
) -> None:
    sudo_prefix = _sudo_prefix(runner=runner)
    if sudo_prefix is None:
        _record_warning(
            session,
            log,
            "sudo is unavailable; system USB pollers remain active",
        )
        for unit in QUIET_SYSTEM_UNITS:
            session.system_units.append((unit, _system_unit_state(unit, runner=runner)))
        return

    for unit in QUIET_SYSTEM_UNITS:
        state = _system_unit_state(unit, runner=runner)
        session.system_units.append((unit, state))
        if state not in ACTIVE_SYSTEMD_STATES:
            continue
        result = _run(runner, [*sudo_prefix, "systemctl", "stop", unit])
        if result.returncode != 0:
            _record_warning(session, log, f"could not stop {unit}")


def _restore_system_units(
    session: QuietUsbSession,
    *,
    runner: Runner,
    log: Logger | None,
) -> None:
    sudo_prefix = _sudo_prefix(runner=runner)
    if sudo_prefix is None:
        active_units = [
            unit for unit, state in session.system_units if state in ACTIVE_SYSTEMD_STATES
        ]
        if active_units:
            _record_warning(
                session,
                log,
                "sudo is unavailable; system USB pollers were not restarted",
            )
        return

    for unit, state in session.system_units:
        if state not in ACTIVE_SYSTEMD_STATES:
            continue
        result = _run(runner, [*sudo_prefix, "systemctl", "start", unit])
        if result.returncode != 0:
            _record_warning(session, log, f"could not restart {unit}")


def _pause_user_units(
    session: QuietUsbSession,
    *,
    runner: Runner,
    log: Logger | None,
) -> None:
    for unit in QUIET_USER_UNITS:
        state = _user_unit_state(unit, runner=runner)
        session.user_units.append((unit, state))
        if state not in ACTIVE_SYSTEMD_STATES:
            continue
        result = _run(runner, ["systemctl", "--user", "stop", unit])
        if result.returncode != 0:
            _record_warning(session, log, f"could not stop user {unit}")


def _restore_user_units(
    session: QuietUsbSession,
    *,
    runner: Runner,
    log: Logger | None,
) -> None:
    for unit, state in session.user_units:
        if state not in ACTIVE_SYSTEMD_STATES:
            continue
        result = _run(runner, ["systemctl", "--user", "start", unit])
        if result.returncode != 0:
            _record_warning(session, log, f"could not restart user {unit}")


def _install_bastion_refresh_hold(
    session: QuietUsbSession,
    *,
    runner: Runner,
    log: Logger | None,
) -> None:
    sudo_prefix = _sudo_prefix(runner=runner)
    if sudo_prefix is None:
        session.bastion_hold_prior_state = "unknown"
        _record_warning(
            session,
            log,
            "sudo is unavailable; bastion USB refresh hold was not installed",
        )
        return

    test_result = _run(runner, [*sudo_prefix, "test", "-e", str(BASTION_USB_REFRESH_HOLD)])
    session.bastion_hold_prior_state = "present" if test_result.returncode == 0 else "absent"
    if session.bastion_hold_prior_state == "absent":
        install_result = _run(
            runner,
            [
                *sudo_prefix,
                "install",
                "-d",
                "-m",
                "0755",
                str(BASTION_USB_REFRESH_HOLD.parent),
            ],
        )
        if install_result.returncode != 0:
            _record_warning(session, log, "could not create bastion USB hold directory")
            return

    token_dir_result = _run(
        runner,
        [
            *sudo_prefix,
            "install",
            "-d",
            "-m",
            "0755",
            str(BASTION_USB_REFRESH_HOLD_TOKENS),
        ],
    )
    if token_dir_result.returncode != 0:
        _record_warning(session, log, "could not create bastion USB hold token directory")
        return

    if session.bastion_hold_prior_state == "present":
        token_check = _run(
            runner,
            [
                *sudo_prefix,
                "find",
                str(BASTION_USB_REFRESH_HOLD_TOKENS),
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-print",
                "-quit",
            ],
        )
        if token_check.returncode == 0 and token_check.stdout.strip():
            session.bastion_hold_prior_state = "managed"

    token_path = BASTION_USB_REFRESH_HOLD_TOKENS / (
        f"{os.getpid()}-{uuid.uuid4().hex}.hold"
    )
    touch_token_result = _run(runner, [*sudo_prefix, "touch", str(token_path)])
    if touch_token_result.returncode != 0:
        _record_warning(session, log, "could not create bastion USB hold token")
        return
    session.bastion_hold_token_path = token_path

    if session.bastion_hold_prior_state in {"present", "managed"}:
        return

    touch_result = _run(runner, [*sudo_prefix, "touch", str(BASTION_USB_REFRESH_HOLD)])
    if touch_result.returncode != 0:
        _record_warning(session, log, "could not install bastion USB refresh hold")


def _release_bastion_refresh_hold(
    session: QuietUsbSession,
    *,
    runner: Runner,
    log: Logger | None,
) -> None:
    sudo_prefix = _sudo_prefix(runner=runner)
    if sudo_prefix is None:
        _record_warning(
            session,
            log,
            "sudo is unavailable; bastion USB refresh hold was not removed",
        )
        return
    if session.bastion_hold_token_path is not None:
        token_result = _run(
            runner,
            [*sudo_prefix, "rm", "-f", str(session.bastion_hold_token_path)],
        )
        if token_result.returncode != 0:
            _record_warning(session, log, "could not remove bastion USB hold token")
            return
    if session.bastion_hold_prior_state not in {"absent", "managed"}:
        return
    token_check = _run(
        runner,
        [
            *sudo_prefix,
            "find",
            str(BASTION_USB_REFRESH_HOLD_TOKENS),
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-print",
            "-quit",
        ],
    )
    if token_check.returncode == 0 and token_check.stdout.strip():
        return
    result = _run(runner, [*sudo_prefix, "rm", "-f", str(BASTION_USB_REFRESH_HOLD)])
    if result.returncode != 0:
        _record_warning(session, log, "could not remove bastion USB refresh hold")


def _run_quiet_usb_step(
    label: str,
    action: Callable[[], None],
    session: QuietUsbSession,
    log: Logger | None,
) -> None:
    try:
        action()
    except Exception as exc:
        _record_warning(session, log, f"failed to {label}: {exc}")


@contextmanager
def _exclusive_quiet_usb_window(
    session: QuietUsbSession,
    log: Logger | None,
) -> Iterator[None]:
    lock_path = Path(os.environ.get("ARTHEXIS_QUIET_USB_LOCK", str(QUIET_USB_LOCK)))
    lock_file = None
    thread_lock_acquired = False
    try:
        import fcntl

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _QUIET_USB_THREAD_LOCK.acquire()
        thread_lock_acquired = True
        lock_file = lock_path.open("a", encoding="utf-8")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        if lock_file is not None:
            lock_file.close()
        if thread_lock_acquired:
            _QUIET_USB_THREAD_LOCK.release()
        _record_warning(session, log, f"could not acquire quiet USB lock: {exc}")
        yield
        return

    try:
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            _QUIET_USB_THREAD_LOCK.release()


@contextmanager
def quiet_usb_pollers(
    *,
    log: Logger | None = None,
    enabled: bool | None = None,
    runner: Runner = subprocess.run,
) -> Iterator[QuietUsbSession]:
    """Pause host USB pollers around a destructive SD-card write.

    This helper is intentionally best-effort. It must never make a burn less safe
    by failing before the writer's own block-device checks run.
    """

    use_quiet_mode = quiet_usb_enabled() if enabled is None else enabled
    session = QuietUsbSession(enabled=bool(use_quiet_mode))
    if not use_quiet_mode or os.name == "nt":
        yield session
        return
    if not _systemctl_available(runner=runner):
        _record_warning(session, log, "systemctl is unavailable; quiet USB skipped")
        yield session
        return

    with _exclusive_quiet_usb_window(session, log):
        _log(log, "quiet-usb: pausing local USB pollers for burn window")
        _run_quiet_usb_step(
            "install bastion refresh hold",
            lambda: _install_bastion_refresh_hold(session, runner=runner, log=log),
            session,
            log,
        )
        _run_quiet_usb_step(
            "pause system units",
            lambda: _pause_system_units(session, runner=runner, log=log),
            session,
            log,
        )
        _run_quiet_usb_step(
            "pause user units",
            lambda: _pause_user_units(session, runner=runner, log=log),
            session,
            log,
        )
        try:
            yield session
        finally:
            _log(log, "quiet-usb: restoring local USB pollers")
            _run_quiet_usb_step(
                "restore user units",
                lambda: _restore_user_units(session, runner=runner, log=log),
                session,
                log,
            )
            _run_quiet_usb_step(
                "restore system units",
                lambda: _restore_system_units(session, runner=runner, log=log),
                session,
                log,
            )
            _run_quiet_usb_step(
                "release bastion refresh hold",
                lambda: _release_bastion_refresh_hold(session, runner=runner, log=log),
                session,
                log,
            )


__all__ = [
    "BASTION_USB_REFRESH_HOLD",
    "BASTION_USB_REFRESH_HOLD_TOKENS",
    "QUIET_SYSTEM_UNITS",
    "QUIET_USB_LOCK",
    "QUIET_USB_ENV",
    "QUIET_USER_UNITS",
    "QuietUsbSession",
    "quiet_usb_enabled",
    "quiet_usb_pollers",
]
