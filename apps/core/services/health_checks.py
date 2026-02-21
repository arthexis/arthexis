"""Reusable health checks for the core app."""

from __future__ import annotations

import io
import json
import math
import random
import string
import subprocess
import time
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.utils import timezone

from apps.cards.reader import validate_rfid_value
from apps.core.notifications import NotificationManager, notify
from apps.core.system.ui import _format_timestamp
from apps.core.system.upgrade import (
    _auto_upgrade_next_check,
    _get_auto_upgrade_periodic_task,
    _load_auto_upgrade_skip_revisions,
    _read_auto_upgrade_mode,
)
from apps.screens.lcd import LCDUnavailableError, prepare_lcd_controller
from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE, render_lcd_lock_file
from apps.users.system import collect_system_user_issues, ensure_system_user


def run_check_time(*, stdout, style, **_kwargs) -> None:
    """Print the current server time."""

    current_time = timezone.localtime()
    stdout.write(style.SUCCESS(f"Current server time: {current_time.isoformat()}"))


def _collect_admin_issues(user) -> Iterable[str]:
    if getattr(user, "is_deleted", False):
        yield "account is marked as deleted"
    if not getattr(user, "is_active", True):
        yield "account is inactive"
    if not getattr(user, "is_staff", True):
        yield "account is not marked as staff"
    if not getattr(user, "is_superuser", True):
        yield "account is not a superuser"
    if not user.password:
        yield "account does not have a password"
    elif not user.has_usable_password():
        yield "account password is unusable"


def _resolve_system_delegate(user):
    user_model = type(user)
    system_username = getattr(user_model, "SYSTEM_USERNAME", "")
    if not system_username:
        return None
    manager = getattr(user_model, "all_objects", user_model._default_manager)
    delegate = manager.filter(username=system_username).exclude(pk=user.pk).first()
    if delegate is None:
        return None
    if not getattr(delegate, "is_staff", True) or not getattr(delegate, "is_superuser", True):
        return None
    return delegate


def _repair_admin(user) -> set[str]:
    updated: set[str] = set()
    if getattr(user, "is_deleted", False):
        user.is_deleted = False
        updated.add("is_deleted")
    if not getattr(user, "is_active", True):
        user.is_active = True
        updated.add("is_active")
    if not getattr(user, "is_staff", True):
        user.is_staff = True
        updated.add("is_staff")
    if not getattr(user, "is_superuser", True):
        user.is_superuser = True
        updated.add("is_superuser")
    delegate = _resolve_system_delegate(user)
    if delegate is not None and user.operate_as_id in {None, user.pk}:
        user.operate_as = delegate
        updated.add("operate_as")
    if not user.password or not user.has_usable_password() or not user.check_password("admin"):
        user.set_password("admin")
        updated.add("password")
    return updated


def _create_admin(user_model, username):
    user = user_model.all_objects.create(
        username=username,
        is_staff=True,
        is_superuser=True,
        is_active=True,
    )
    user.set_password("admin")
    delegate = _resolve_system_delegate(user)
    if delegate is not None:
        user.operate_as = delegate
    user.save()
    from apps.locals.models import ensure_admin_favorites

    ensure_admin_favorites(user)
    return user


def run_check_admin(*, stdout, style, force: bool = False, **_kwargs) -> None:
    """Validate that the default admin account is available."""

    user_model = get_user_model()
    username = getattr(user_model, "ADMIN_USERNAME", "admin")
    if not username:
        raise CommandError("The user model does not define an admin username.")

    manager = getattr(user_model, "all_objects", user_model._default_manager)
    user = manager.filter(username=username).first()

    if user is None:
        if not force:
            raise CommandError(
                f"No account exists for username {username!r}. Use --force to create it."
            )
        _create_admin(user_model, username)
        stdout.write(style.SUCCESS(f"Created default admin account {username!r}."))
        return

    issues = list(_collect_admin_issues(user))
    if issues and not force:
        buffer = io.StringIO()
        buffer.write(
            f"Issues detected with the {username!r} account. Use --force to repair it.\n"
        )
        for issue in issues:
            buffer.write(f" - {issue}\n")
        raise CommandError(buffer.getvalue().rstrip())

    if force:
        updated = _repair_admin(user)
        if updated:
            user.save(update_fields=sorted(updated))
            stdout.write(
                style.SUCCESS(
                    f"Repaired default admin account {username!r}: {', '.join(sorted(updated))}."
                )
            )
        else:
            stdout.write(style.SUCCESS(f"Default admin account {username!r} is already healthy."))
        return

    stdout.write(style.SUCCESS(f"Default admin account {username!r} is healthy."))


def run_check_system_user(*, stdout, style, force: bool = False, **_kwargs) -> None:
    """Validate that the system account is available and secured."""

    user_model = get_user_model()
    username = getattr(user_model, "SYSTEM_USERNAME", "")
    if not username:
        raise CommandError("The user model does not define a system username.")

    manager = getattr(user_model, "all_objects", user_model._default_manager)
    user = manager.filter(username=username).first()

    if user is None:
        if not force:
            raise CommandError(
                f"No account exists for username {username!r}. Use --force to create it."
            )
        ensure_system_user()
        stdout.write(style.SUCCESS(f"Created system account {username!r}."))
        return

    issues = list(collect_system_user_issues(user))
    if issues and not force:
        buffer = io.StringIO()
        buffer.write(
            f"Issues detected with the {username!r} account. Use --force to repair it.\n"
        )
        for issue in issues:
            buffer.write(f" - {issue}\n")
        raise CommandError(buffer.getvalue().rstrip())

    if force:
        _user, updated = ensure_system_user(record_updates=True)
        if updated:
            stdout.write(
                style.SUCCESS(
                    f"Repaired system account {username!r}: {', '.join(sorted(updated))}."
                )
            )
        else:
            stdout.write(style.SUCCESS(f"System account {username!r} is already healthy."))
        return

    stdout.write(style.SUCCESS(f"System account {username!r} is healthy."))


def run_check_rfid(*, stdout, rfid_value: str | None = None, rfid_kind: str | None = None, rfid_pretty: bool = False, **_kwargs) -> None:
    """Validate a manually entered RFID value using scanner logic."""

    if not rfid_value:
        raise CommandError("The RFID check requires --rfid-value.")
    result = validate_rfid_value(rfid_value, kind=rfid_kind)
    if "error" in result:
        raise CommandError(result["error"])
    dump_kwargs = {"indent": 2, "sort_keys": True} if rfid_pretty else {}
    stdout.write(json.dumps(result, **dump_kwargs))


def run_check_next_upgrade(*, stdout, **_kwargs) -> None:
    """Display information about next and previous auto-upgrade checks."""

    base_dir = Path(settings.BASE_DIR)
    now = timezone.now()
    task, available, error = _get_auto_upgrade_periodic_task()
    schedule = task.schedule if task else None

    next_run_dt = None
    if task and schedule is not None:
        try:
            now_schedule = schedule.maybe_make_aware(schedule.now())
            start_time = getattr(task, "start_time", None)
            if start_time is not None:
                start_time = schedule.maybe_make_aware(start_time)
            reference = getattr(task, "last_run_at", None)
            if reference is not None:
                reference = schedule.maybe_make_aware(reference)
            if start_time and start_time > now_schedule:
                next_run_dt = start_time
            else:
                if reference is None:
                    reference = now_schedule
                remaining = schedule.remaining_estimate(reference)
                next_run_dt = now_schedule + remaining
        except Exception:
            next_run_dt = None

    last_run_dt = getattr(task, "last_run_at", None) if task else None
    if last_run_dt is not None and timezone.is_naive(last_run_dt):
        try:
            last_run_dt = timezone.make_aware(last_run_dt, timezone.get_current_timezone())
        except Exception:
            pass

    next_display = _format_timestamp(next_run_dt) if next_run_dt is not None else _auto_upgrade_next_check()
    if not next_display:
        next_display = "Unavailable"
    last_display = _format_timestamp(last_run_dt) if last_run_dt is not None else "Unavailable"

    mode_info = _read_auto_upgrade_mode(base_dir)
    mode_value = str(mode_info.get("mode", "version"))
    mode_enabled = bool(mode_info.get("enabled", False))

    skip_revisions = _load_auto_upgrade_skip_revisions(base_dir)
    blockers: list[str] = []
    if not available:
        blockers.append(error or "Auto-upgrade scheduling information is unavailable.")
    elif not task:
        blockers.append("The auto-upgrade periodic task has not been created.")
    else:
        if not getattr(task, "enabled", False):
            blockers.append("The auto-upgrade periodic task is disabled.")
        elif schedule is None:
            blockers.append("The auto-upgrade schedule configuration could not be read.")
    if not mode_info.get("enabled"):
        blockers.append("No upgrade policies apply; manual upgrades required.")

    def _minutes_until(target, current):
        if target is None:
            return None
        delta = (target - current).total_seconds()
        if delta <= 0:
            return 0
        return int(math.ceil(delta / 60))

    def _minutes_since(target, current):
        if target is None:
            return None
        delta = (current - target).total_seconds()
        if delta <= 0:
            return 0
        return int(delta // 60)

    next_minutes = _minutes_until(next_run_dt, now)
    previous_minutes = _minutes_since(last_run_dt, now)

    stdout.write(f"Auto-upgrade mode: {'enabled' if mode_enabled else 'disabled'} ({mode_value})")
    if next_minutes is None:
        stdout.write(f"Next upgrade check: {next_display}")
    elif next_minutes == 0:
        stdout.write(f"Next upgrade check: {next_display} (due now)")
    else:
        suffix = f"in ~{next_minutes} minute{'s' if next_minutes != 1 else ''}"
        stdout.write(f"Next upgrade check: {next_display} ({suffix})")

    if previous_minutes is None or last_display == "Unavailable":
        stdout.write(f"Previous upgrade check: {last_display}")
    elif previous_minutes == 0:
        stdout.write(f"Previous upgrade check: {last_display} (just now)")
    else:
        suffix = f"~{previous_minutes} minute{'s' if previous_minutes != 1 else ''} ago"
        stdout.write(f"Previous upgrade check: {last_display} ({suffix})")

    if skip_revisions:
        stdout.write("Blocked revisions:")
        for revision in skip_revisions:
            stdout.write(f" - {revision}")
    else:
        stdout.write("Blocked revisions: none recorded.")

    if blockers:
        stdout.write("Blockers detected:")
        for blocker in blockers:
            stdout.write(f" - {blocker}")
    else:
        stdout.write("Blockers: none detected.")


def run_check_lcd_send(
    *,
    stdout,
    style,
    lcd_subject: str | None = None,
    lcd_body: str = "",
    lcd_expires_at: str | None = None,
    lcd_sticky: bool = False,
    lcd_channel_type: str | None = None,
    lcd_channel_num: str | None = None,
    lcd_timeout: float = 10.0,
    lcd_poll_interval: float = 0.2,
    **_kwargs,
) -> None:
    """Send a test message to the LCD and validate lock-file handling."""

    if not lcd_subject:
        raise CommandError("The LCD send check requires --lcd-subject.")

    base_dir = Path(settings.BASE_DIR)
    manager = NotificationManager(lock_dir=base_dir / ".locks")
    lock_file = manager.get_target_lock_file(
        channel_type=lcd_channel_type,
        channel_num=lcd_channel_num,
        sticky=lcd_sticky,
    )
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    normalized_type = manager._normalize_channel_type(lcd_channel_type, sticky=lcd_sticky)
    expected_payload = None
    if normalized_type != "event":
        expected_payload = render_lcd_lock_file(
            subject=lcd_subject,
            body=lcd_body,
            expires_at=lcd_expires_at,
        )

    stdout.write(f"Sending test message to LCD: subject='{lcd_subject}' body='{lcd_body}'")
    stdout.write(f"Target lock file: {lock_file}")
    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    manager.send(
        subject=lcd_subject,
        body=lcd_body,
        sticky=lcd_sticky,
        expires_at=lcd_expires_at,
        channel_type=lcd_channel_type,
        channel_num=lcd_channel_num,
    )

    def _payload_matches() -> bool:
        if not lock_file.exists():
            return False
        if expected_payload is None:
            return True
        try:
            return lock_file.read_text(encoding="utf-8") == expected_payload
        except OSError:
            return False

    deadline = time.monotonic() + lcd_timeout
    while time.monotonic() < deadline and not _payload_matches():
        time.sleep(lcd_poll_interval)
    if not _payload_matches():
        raise CommandError("Lock file was not written by notification helper")

    stdout.write(style.SUCCESS("Lock file written with test message"))

    deadline = time.monotonic() + lcd_timeout
    while time.monotonic() < deadline:
        if not _payload_matches():
            raise CommandError("LCD daemon did not keep the lock file message sticky")
        time.sleep(lcd_poll_interval)
    stdout.write(style.SUCCESS("LCD daemon kept the lock file message sticky"))


def run_check_lcd_service(*, stdout, style, lcd_confirmed: bool = False, **_kwargs) -> None:
    """Validate LCD service setup and display a test message."""

    base_dir = Path(settings.BASE_DIR)
    lock_file = base_dir / ".locks" / LCD_LOW_LOCK_FILE
    service_file = base_dir / ".locks" / "service.lck"

    stdout.write("LCD diagnostic report:")
    if lock_file.exists():
        content = lock_file.read_text(encoding="utf-8").strip()
        if content:
            stdout.write(style.SUCCESS("Lock file exists and contains data"))
        else:
            stdout.write(style.WARNING("Lock file is empty; startup trigger may not have executed"))
    else:
        stdout.write(style.ERROR("Lock file missing; LCD service may not be running"))

    if service_file.exists():
        service_name = service_file.read_text(encoding="utf-8").strip()
        lcd_service = f"lcd-{service_name}"
        try:
            result = subprocess.run(["systemctl", "is-active", lcd_service], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == "active":
                stdout.write(style.SUCCESS(f"Service {lcd_service} is active"))
            else:
                stdout.write(style.ERROR(f"Service {lcd_service} is not active"))
        except FileNotFoundError:
            stdout.write(style.WARNING("systemctl not available; cannot verify service"))
    else:
        stdout.write(style.WARNING("Service lock file missing; cannot determine LCD service"))

    try:
        prepare_lcd_controller(base_dir=base_dir)
        stdout.write(style.SUCCESS("I2C communication with LCD succeeded"))
    except LCDUnavailableError:
        stdout.write(style.ERROR("LCDUnavailableError: cannot access I2C bus"))
    except FileNotFoundError as exc:
        stdout.write(style.ERROR(f"Unexpected error during LCD init: {exc}"))
        if "/dev/i2c-1" in str(exc):
            stdout.write(
                style.WARNING(
                    "Hint: enable the I2C interface or check that the LCD is wired correctly. "
                    "On Raspberry Pi, run 'sudo raspi-config' then enable I2C under Interfacing Options"
                )
            )
    random_text = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    notify(subject=random_text)
    stdout.write(f"Displayed random string: {random_text}")
    if lcd_confirmed:
        stdout.write(style.SUCCESS("LCD display confirmed"))
        return
    stdout.write(style.WARNING("LCD display not confirmed by user; run with --lcd-confirmed once validated"))
