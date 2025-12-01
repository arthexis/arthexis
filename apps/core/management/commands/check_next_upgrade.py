from __future__ import annotations

import math
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core import system


class Command(BaseCommand):
    """Display information about the next scheduled auto-upgrade check."""

    help = "Show the next and previous auto-upgrade checks along with blockers."

    def handle(self, *args, **options):  # noqa: D401 - inherited docstring
        base_dir = Path(settings.BASE_DIR)
        now = timezone.now()
        task, available, error = system._get_auto_upgrade_periodic_task()
        schedule = self._resolve_schedule(task)

        next_run_dt = self._estimate_next_run(task, schedule) if task else None
        last_run_dt = self._coerce_aware(getattr(task, "last_run_at", None), schedule)

        next_display = "Unavailable"
        if next_run_dt is not None:
            next_display = system._format_timestamp(next_run_dt) or "Unavailable"
        else:
            # Fall back to the legacy formatter so disabled schedules still display
            fallback = system._auto_upgrade_next_check()
            next_display = fallback or next_display

        last_display = (
            system._format_timestamp(last_run_dt)
            if last_run_dt is not None
            else ""
        )
        if not last_display:
            last_display = "Unavailable"

        mode_info = system._read_auto_upgrade_mode(base_dir)
        mode_value = str(mode_info.get("mode", "version"))
        mode_enabled = bool(mode_info.get("enabled", False))

        skip_revisions = system._load_auto_upgrade_skip_revisions(base_dir)
        blockers = self._collect_blockers(
            base_dir,
            available,
            error,
            task,
            mode_info,
            schedule,
        )

        next_minutes = self._minutes_until(next_run_dt, now)
        previous_minutes = self._minutes_since(last_run_dt, now)

        self.stdout.write(f"Auto-upgrade mode: {'enabled' if mode_enabled else 'disabled'} ({mode_value})")

        next_suffix = self._format_minutes_suffix(next_minutes, future=True)
        if next_suffix:
            self.stdout.write(f"Next upgrade check: {next_display} ({next_suffix})")
        else:
            self.stdout.write(f"Next upgrade check: {next_display}")

        prev_suffix = self._format_minutes_suffix(previous_minutes, future=False)
        if prev_suffix and last_display != "Unavailable":
            self.stdout.write(
                f"Previous upgrade check: {last_display} ({prev_suffix})"
            )
        else:
            self.stdout.write(f"Previous upgrade check: {last_display}")

        if skip_revisions:
            self.stdout.write("Blocked revisions:")
            for revision in skip_revisions:
                self.stdout.write(f" - {revision}")
        else:
            self.stdout.write("Blocked revisions: none recorded.")

        if blockers:
            self.stdout.write("Blockers detected:")
            for blocker in blockers:
                self.stdout.write(f" - {blocker}")
        else:
            self.stdout.write("Blockers: none detected.")

    def _resolve_schedule(self, task):
        if not task:
            return None
        try:
            return task.schedule
        except Exception:
            return None

    def _estimate_next_run(self, task, schedule):
        if not task or schedule is None:
            return None
        try:
            now = schedule.maybe_make_aware(schedule.now())
        except Exception:
            return None

        start_time = self._coerce_aware(getattr(task, "start_time", None), schedule)
        if start_time and start_time > now:
            return start_time

        reference = self._coerce_aware(getattr(task, "last_run_at", None), schedule)
        if reference is None:
            reference = now

        try:
            remaining = schedule.remaining_estimate(reference)
        except Exception:
            return None

        try:
            return now + remaining
        except Exception:
            return None

    def _coerce_aware(self, dt, schedule):
        if dt is None:
            return None
        if schedule is not None:
            try:
                aware = schedule.maybe_make_aware(dt)
            except Exception:
                aware = None
            else:
                if aware is not None:
                    return aware
        if timezone.is_naive(dt):
            try:
                return timezone.make_aware(dt, timezone.get_current_timezone())
            except Exception:
                return dt
        return dt

    def _collect_blockers(
        self,
        base_dir: Path,
        available: bool,
        error: str,
        task,
        mode_info: dict,
        schedule,
    ) -> list[str]:
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

        mode_file = system._auto_upgrade_mode_file(base_dir)
        if not mode_info.get("enabled"):
            if not mode_info.get("lock_exists"):
                blockers.append(
                    f"Auto-upgrade is disabled because {mode_file} does not exist."
                )
            elif mode_info.get("read_error"):
                blockers.append(
                    f"Auto-upgrade mode file {mode_file} exists but could not be read."
                )

        return blockers

    def _minutes_until(self, target, now):
        if target is None:
            return None
        delta = (target - now).total_seconds()
        if delta <= 0:
            return 0
        return int(math.ceil(delta / 60))

    def _minutes_since(self, target, now):
        if target is None:
            return None
        delta = (now - target).total_seconds()
        if delta <= 0:
            return 0
        return int(delta // 60)

    def _format_minutes_suffix(self, minutes, *, future: bool) -> str:
        if minutes is None:
            return ""
        if future:
            if minutes == 0:
                return "due now"
            return f"in ~{minutes} minute{'s' if minutes != 1 else ''}"
        if minutes == 0:
            return "just now"
        return f"~{minutes} minute{'s' if minutes != 1 else ''} ago"
