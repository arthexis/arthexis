from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core import system
from apps.nodes import tasks as node_tasks


def _lcd_lines(
    uptime_seconds: int | None,
    on_seconds: int | None,
    interface_label: str | None,
    ap_enabled: bool,
) -> tuple[str, str]:
    def _format_duration(seconds: int | None) -> str:
        parts = node_tasks._uptime_components(seconds)
        if parts is None:
            return "?d?h?m"

        days, hours, minutes = parts
        return f"{days}d{hours}h{minutes}m"

    subject_parts = [f"UP {_format_duration(uptime_seconds)}"]
    if ap_enabled:
        subject_parts.append("AP")

    body_parts = [f"ON {node_tasks._format_duration_hms(on_seconds)}"]
    if interface_label:
        body_parts.append(interface_label)

    return " ".join(subject_parts), " ".join(body_parts)


def _lock_heartbeat(lock_path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(lock_path.stat().st_mtime, tz=datetime_timezone.utc)
    except OSError:
        return None


def _lock_issues(
    *,
    lock_info: dict[str, object],
    now: datetime,
    boot_time: datetime | None,
    uptime_seconds: int | None,
) -> list[str]:
    issues: list[str] = []
    lock_path = lock_info.get("path")

    if not lock_info.get("exists"):
        issues.append(f"Suite uptime lock missing at {lock_path}")
        return issues

    started_at = lock_info.get("started_at") if isinstance(lock_info.get("started_at"), datetime) else None
    if started_at is None:
        issues.append("Suite uptime lock is missing a valid started_at timestamp")
    else:
        if started_at > now:
            issues.append("Suite uptime lock reports a start time in the future")
        if boot_time and started_at < boot_time:
            issues.append("Suite uptime lock predates the current boot and may be stale")

    heartbeat = _lock_heartbeat(lock_path) if isinstance(lock_path, Path) else None
    if heartbeat and heartbeat > now.astimezone(datetime_timezone.utc):
        issues.append("Suite uptime lock heartbeat is newer than the current time")

    if lock_info.get("exists") and not lock_info.get("fresh"):
        issues.append("Suite uptime lock heartbeat is stale or missing")

    if uptime_seconds is None:
        issues.append("Unable to calculate uptime from the suite uptime lock")

    return issues


class Command(BaseCommand):
    help = "Display suite uptime and lockfile status"

    def handle(self, *args, **options):  # type: ignore[override]
        now = timezone.now()
        base_dir = Path(settings.BASE_DIR)

        lock_info = system._suite_uptime_lock_info(now=now)
        lock_path = lock_info.get("path")
        boot_time = system._system_boot_time(now)
        uptime_seconds = node_tasks._startup_duration_seconds(base_dir)
        on_seconds = node_tasks._availability_seconds(base_dir)
        subject_line, body_line = _lcd_lines(
            uptime_seconds,
            on_seconds,
            node_tasks._active_interface_label(),
            node_tasks._ap_mode_enabled(),
        )

        started_at = lock_info.get("started_at") if isinstance(lock_info.get("started_at"), datetime) else None
        heartbeat = _lock_heartbeat(lock_path) if isinstance(lock_path, Path) else None
        uptime_details = system._suite_uptime_details()

        self.stdout.write("Suite uptime (LCD format):")
        self.stdout.write(f"  {subject_line}")
        self.stdout.write(f"  {body_line}")
        self.stdout.write("")

        self.stdout.write("Details:")
        self.stdout.write(f"  Lock path: {lock_path}")
        self.stdout.write(f"  Started at: {system._format_datetime(started_at) or 'unknown'}")
        self.stdout.write(
            f"  Heartbeat: {system._format_datetime(heartbeat) or 'unknown'}"
        )
        self.stdout.write(
            f"  Suite uptime: {uptime_details.get('uptime') or 'unavailable'}"
        )
        self.stdout.write(
            f"  Suite boot time: {system._format_datetime(uptime_details.get('boot_time')) or 'unknown'}"
        )
        if boot_time:
            self.stdout.write(
                f"  System boot time: {system._format_datetime(boot_time) or 'unknown'}"
            )

        issues = _lock_issues(
            lock_info=lock_info,
            now=now,
            boot_time=boot_time,
            uptime_seconds=uptime_seconds,
        )

        self.stdout.write("")
        if issues:
            self.stdout.write("Warnings:")
            for issue in issues:
                self.stdout.write(f"  - {issue}")
        else:
            self.stdout.write("Uptime lock status: OK")
