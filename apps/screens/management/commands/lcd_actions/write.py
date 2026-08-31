from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from argparse import SUPPRESS
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.nodes.feature_detection import is_local_node_feature_active
from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LcdMessage,
    read_lcd_lock_file,
    render_lcd_lock_file,
)
from apps.sigils.sigil_resolver import resolve_sigils

IMPORTANT_REPEATER_STATE_NAME = "lcd-important-repeater.json"
IMPORTANT_REPEATER_LOG_NAME = "lcd-important-repeater.log"
DEFAULT_IMPORTANT_DISPLAY_SECONDS = 60.0
DEFAULT_IMPORTANT_REPEAT_SECONDS = 180.0
DEFAULT_IMPORTANT_REFRESH_SECONDS = 15.0


class Command(BaseCommand):
    """Update the LCD lock file or restart the LCD updater service."""

    help = "Write subject/body to the lcd lock file, delete it, or restart the updater"

    def add_arguments(self, parser):
        parser.add_argument("--subject", help="First LCD line (max 64 chars)")
        parser.add_argument("--body", help="Second LCD line (max 64 chars)")
        parser.add_argument(
            "--sticky",
            action="store_true",
            help="Write to the high-priority LCD lock instead of the low lock",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete the lcd lock file instead of writing to it",
        )
        parser.add_argument(
            "--important",
            action="store_true",
            help=(
                "Start or replace the singleton repeating high-priority LCD "
                "notification."
            ),
        )
        parser.add_argument(
            "--stop-important",
            action="store_true",
            help=(
                "Stop the singleton repeating important LCD notification and "
                "clear its owned sticky lock."
            ),
        )
        parser.add_argument(
            "--display-seconds",
            type=float,
            default=DEFAULT_IMPORTANT_DISPLAY_SECONDS,
            help=(
                "Seconds each important notification display window remains "
                f"active (default: {int(DEFAULT_IMPORTANT_DISPLAY_SECONDS)})."
            ),
        )
        parser.add_argument(
            "--repeat-seconds",
            type=float,
            default=DEFAULT_IMPORTANT_REPEAT_SECONDS,
            help=(
                "Seconds between important notification display window starts "
                f"(default: {int(DEFAULT_IMPORTANT_REPEAT_SECONDS)})."
            ),
        )
        parser.add_argument(
            "--refresh-seconds",
            type=float,
            default=DEFAULT_IMPORTANT_REFRESH_SECONDS,
            help=(
                "Seconds between sticky-lock refreshes while an important "
                f"window is active (default: {int(DEFAULT_IMPORTANT_REFRESH_SECONDS)})."
            ),
        )
        parser.add_argument(
            "--run-important-repeater",
            action="store_true",
            help=SUPPRESS,
        )
        parser.add_argument(
            "--repeater-token",
            dest="important_repeater_token",
            default="",
            help=SUPPRESS,
        )
        parser.add_argument(
            "--restart",
            action="store_true",
            help="Restart the lcd updater service after modifying the lock file",
        )
        parser.add_argument(
            "--no-resolve",
            dest="resolve_sigils",
            action="store_false",
            default=True,
            help="Disable resolving [SIGILS] in subject/body before writing the lock file",
        )
        parser.add_argument(
            "--service",
            dest="service_name",
            help=(
                "Base service name (defaults to the content of .locks/service.lck). "
                "The lcd unit is derived as lcd-<service>."
            ),
        )

    def handle(self, *args, **options):
        if options["restart"]:
            self._ensure_lcd_feature_active()

        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        target_name = (
            LCD_HIGH_LOCK_FILE
            if options.get("sticky") or options.get("important")
            else LCD_LOW_LOCK_FILE
        )
        lock_file = lock_dir / target_name

        if options.get("run_important_repeater"):
            return self._run_important_repeater(base_dir=base_dir, options=options)

        if options.get("stop_important"):
            stopped = stop_important_repeater(base_dir=base_dir, clear_message=True)
            pids = ", ".join(str(pid) for pid in stopped) or "(none)"
            self.stdout.write(
                self.style.SUCCESS(f"Stopped important LCD repeater: {pids}")
            )
        elif options.get("important"):
            if options.get("delete"):
                raise CommandError("--important cannot be combined with --delete")
            subject, body, expires_at = self._resolve_lock_payload(lock_file, options)
            repeater_pid = start_important_repeater(
                base_dir=base_dir,
                subject=subject,
                body=body,
                expires_at=expires_at,
                display_seconds=options["display_seconds"],
                repeat_seconds=options["repeat_seconds"],
                refresh_seconds=options["refresh_seconds"],
            )
            state_path = important_repeater_state_path(base_dir)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Started important LCD repeater {repeater_pid}; state={state_path}"
                )
            )
        elif options["delete"]:
            self._delete_lock_file(lock_file)
        else:
            self._write_lock_file(lock_dir, lock_file, options)

        if options["restart"]:
            self._restart_service(
                base_dir=base_dir, service_name=options.get("service_name")
            )

    # ------------------------------------------------------------------
    def _ensure_lcd_feature_active(self) -> None:
        """Raise when lcd-screen is unavailable for this node."""

        if not is_local_node_feature_active("lcd-screen"):
            raise CommandError("lcd-screen feature is not active on this node")

    def _delete_lock_file(self, lock_file: Path) -> None:
        if lock_file.exists():
            lock_file.unlink()
            self.stdout.write(self.style.SUCCESS(f"Deleted {lock_file}"))
        else:
            self.stdout.write(self.style.WARNING(f"Lock file not found: {lock_file}"))

    def _write_lock_file(self, lock_dir: Path, lock_file: Path, options: dict) -> None:
        subject, body, expires_at = self._resolve_lock_payload(lock_file, options)
        write_lcd_lock_payload(
            lock_dir=lock_dir,
            lock_file=lock_file,
            subject=subject,
            body=body,
            expires_at=expires_at,
        )
        self.stdout.write(self.style.SUCCESS(f"Updated {lock_file}"))

    def _resolve_lock_payload(
        self, lock_file: Path, options: dict
    ) -> tuple[str, str, object]:
        existing = read_lcd_lock_file(lock_file) or self._default_lock_payload()
        subject = (
            options.get("subject")
            if options.get("subject") is not None
            else existing.subject
        )
        body = options.get("body") if options.get("body") is not None else existing.body
        expires_at = None if options.get("important") else existing.expires_at

        if options.get("resolve_sigils"):
            subject = resolve_sigils(subject)
            body = resolve_sigils(body)

        return subject, body, expires_at

    def _default_lock_payload(self) -> LcdMessage:
        return LcdMessage(subject="", body="")

    def _run_important_repeater(self, *, base_dir: Path, options: dict) -> int:
        subject = options.get("subject")
        body = options.get("body")
        if subject is None or body is None:
            raise CommandError("--run-important-repeater requires --subject and --body")
        return run_important_repeater(
            base_dir=base_dir,
            subject=subject,
            body=body,
            display_seconds=options["display_seconds"],
            repeat_seconds=options["repeat_seconds"],
            refresh_seconds=options["refresh_seconds"],
            token=options.get("important_repeater_token") or "",
        )

    def _restart_service(self, *, base_dir: Path, service_name: str | None) -> None:
        resolved_service = service_name or self._read_service_name(base_dir)
        if not resolved_service:
            raise CommandError("Service name is required to restart the lcd updater")

        lcd_unit = f"lcd-{resolved_service}"
        try:
            result = subprocess.run(
                ["systemctl", "restart", lcd_unit],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CommandError(
                "systemctl not available; cannot restart lcd service"
            ) from exc

        if result.returncode != 0:
            error_output = (result.stderr or result.stdout or "Unknown error").strip()
            raise CommandError(f"Failed to restart {lcd_unit}: {error_output}")

        self.stdout.write(self.style.SUCCESS(f"Restarted {lcd_unit}"))

    def _read_service_name(self, base_dir: Path) -> str | None:
        service_file = base_dir / ".locks" / "service.lck"
        try:
            return service_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None


def write_lcd_lock_payload(
    *,
    lock_dir: Path,
    lock_file: Path,
    subject: str,
    body: str,
    expires_at: datetime | None = None,
) -> None:
    lock_dir.mkdir(parents=True, exist_ok=True)
    payload = render_lcd_lock_file(subject=subject, body=body, expires_at=expires_at)
    lock_file.write_text(payload, encoding="utf-8")


def normalize_lcd_lock_payload(*, subject: str, body: str) -> tuple[str, str]:
    lines = render_lcd_lock_file(subject=subject, body=body).splitlines()
    normalized_subject = lines[0] if lines else ""
    normalized_body = lines[1] if len(lines) > 1 else ""
    return normalized_subject, normalized_body


def important_repeater_state_path(base_dir: Path) -> Path:
    return base_dir / ".locks" / IMPORTANT_REPEATER_STATE_NAME


def important_repeater_log_path(base_dir: Path) -> Path:
    return base_dir / "logs" / IMPORTANT_REPEATER_LOG_NAME


def read_important_repeater_state(base_dir: Path) -> dict:
    try:
        payload = json.loads(
            important_repeater_state_path(base_dir).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_important_repeater_state(
    *,
    base_dir: Path,
    pid: int,
    subject: str,
    body: str,
    display_seconds: float,
    repeat_seconds: float,
    refresh_seconds: float,
    token: str,
) -> None:
    state_path = important_repeater_state_path(base_dir)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "subject": subject,
        "body": body,
        "display_seconds": display_seconds,
        "repeat_seconds": repeat_seconds,
        "refresh_seconds": refresh_seconds,
        "token": token,
    }
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def validate_important_repeater_timing(
    *, display_seconds: float, repeat_seconds: float, refresh_seconds: float
) -> None:
    if display_seconds < DEFAULT_IMPORTANT_DISPLAY_SECONDS:
        raise CommandError(
            f"--display-seconds must be at least {int(DEFAULT_IMPORTANT_DISPLAY_SECONDS)}"
        )
    if repeat_seconds < display_seconds:
        raise CommandError("--repeat-seconds must be greater than or equal to display")
    if refresh_seconds <= 0:
        raise CommandError("--refresh-seconds must be greater than zero")


def stop_important_repeater(*, base_dir: Path, clear_message: bool) -> list[int]:
    state = read_important_repeater_state(base_dir)
    stopped: list[int] = []
    try:
        pid = int(state.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0

    remove_state = pid <= 0 or pid == os.getpid() or not _pid_alive(pid)
    if pid > 0 and pid != os.getpid() and not remove_state:
        pid_match = _pid_matches_important_repeater(pid, state, base_dir)
        if pid_match is None:
            raise CommandError(
                "Important LCD repeater PID is alive but ownership could not be "
                f"verified; state preserved at {important_repeater_state_path(base_dir)}"
            )
        if pid_match:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                remove_state = True
            except OSError:
                pass
            else:
                stopped.append(pid)

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.1)
            if _pid_alive(pid):
                try:
                    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                except ProcessLookupError:
                    remove_state = True
                except OSError:
                    pass
            if not _pid_alive(pid):
                remove_state = True
        else:
            remove_state = True

    if remove_state:
        state_path = important_repeater_state_path(base_dir)
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    if remove_state and clear_message and _high_lock_matches(base_dir, state):
        _delete_high_lock(base_dir)

    return stopped


def start_important_repeater(
    *,
    base_dir: Path,
    subject: str,
    body: str,
    expires_at: datetime | None,
    display_seconds: float,
    repeat_seconds: float,
    refresh_seconds: float,
) -> int:
    if expires_at is not None:
        raise CommandError("--important does not support expiring sticky messages")
    subject, body = normalize_lcd_lock_payload(subject=subject, body=body)
    validate_important_repeater_timing(
        display_seconds=display_seconds,
        repeat_seconds=repeat_seconds,
        refresh_seconds=refresh_seconds,
    )
    stop_important_repeater(base_dir=base_dir, clear_message=False)

    manage_py = base_dir / "manage.py"
    if not manage_py.exists():
        raise CommandError(f"manage.py not found at {manage_py}")

    log_path = important_repeater_log_path(base_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    command = [
        sys.executable,
        str(manage_py),
        "lcd",
        "write",
        "--run-important-repeater",
        f"--subject={subject}",
        f"--body={body}",
        "--no-resolve",
        "--display-seconds",
        f"{display_seconds:g}",
        "--repeat-seconds",
        f"{repeat_seconds:g}",
        "--refresh-seconds",
        f"{refresh_seconds:g}",
        f"--repeater-token={token}",
    ]
    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=base_dir,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )
    write_important_repeater_state(
        base_dir=base_dir,
        pid=process.pid,
        subject=subject,
        body=body,
        display_seconds=display_seconds,
        repeat_seconds=repeat_seconds,
        refresh_seconds=refresh_seconds,
        token=token,
    )
    return process.pid


def run_important_repeater(
    *,
    base_dir: Path,
    subject: str,
    body: str,
    display_seconds: float,
    repeat_seconds: float,
    refresh_seconds: float,
    token: str,
) -> int:
    subject, body = normalize_lcd_lock_payload(subject=subject, body=body)
    validate_important_repeater_timing(
        display_seconds=display_seconds,
        repeat_seconds=repeat_seconds,
        refresh_seconds=refresh_seconds,
    )
    stopping = False

    def _handle_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    lock_dir = base_dir / ".locks"
    lock_file = lock_dir / LCD_HIGH_LOCK_FILE
    try:
        while not stopping:
            active_deadline = time.monotonic() + display_seconds
            while not stopping:
                write_lcd_lock_payload(
                    lock_dir=lock_dir,
                    lock_file=lock_file,
                    subject=subject,
                    body=body,
                )
                remaining = active_deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(refresh_seconds, remaining))

            if _state_owned_by_current_process(base_dir, token) and _high_lock_matches(
                base_dir, {"subject": subject, "body": body}
            ):
                _delete_high_lock(base_dir)

            inactive_seconds = max(0.0, repeat_seconds - display_seconds)
            inactive_deadline = time.monotonic() + inactive_seconds
            while not stopping and time.monotonic() < inactive_deadline:
                time.sleep(min(1.0, inactive_deadline - time.monotonic()))
    finally:
        if _state_owned_by_current_process(base_dir, token):
            if _high_lock_matches(base_dir, {"subject": subject, "body": body}):
                _delete_high_lock(base_dir)
            try:
                important_repeater_state_path(base_dir).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _state_owned_by_current_process(base_dir: Path, token: str) -> bool:
    state = read_important_repeater_state(base_dir)
    try:
        pid_matches = int(state.get("pid", 0)) == os.getpid()
    except (TypeError, ValueError):
        return False
    state_token = str(state.get("token") or "")
    return pid_matches and state_token == token


def _pid_matches_important_repeater(
    pid: int, state: dict, base_dir: Path
) -> bool | None:
    command_line = _read_process_command_line(pid)
    if command_line is None:
        return None
    if not command_line or "--run-important-repeater" not in command_line:
        return False

    expected_manage_py = str(base_dir / "manage.py")
    normalized_command = command_line.replace("\\", "/")
    if expected_manage_py.replace("\\", "/") not in normalized_command:
        return False

    for field in ("subject", "body"):
        expected = str(state.get(field) or "")
        if expected and expected not in command_line:
            return False

    state_token = str(state.get("token") or "")
    return not state_token or state_token in command_line


def _read_process_command_line(pid: int) -> str | None:
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        payload = proc_cmdline.read_bytes()
    except OSError:
        payload = b""
    if payload:
        return payload.replace(b"\x00", b" ").decode("utf-8", errors="replace")

    if sys.platform != "win32":
        return None

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process "
                    f"-Filter 'ProcessId = {pid}' | "
                    "Select-Object -ExpandProperty CommandLine"
                ),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _high_lock_matches(base_dir: Path, state: dict) -> bool:
    expected_subject = state.get("subject")
    expected_body = state.get("body")
    if expected_subject is None or expected_body is None:
        return False
    expected_subject, expected_body = normalize_lcd_lock_payload(
        subject=str(expected_subject),
        body=str(expected_body),
    )
    message = read_lcd_lock_file(base_dir / ".locks" / LCD_HIGH_LOCK_FILE)
    if message is None:
        return False
    return message.subject == expected_subject and message.body == expected_body


def _delete_high_lock(base_dir: Path) -> None:
    try:
        (base_dir / ".locks" / LCD_HIGH_LOCK_FILE).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
