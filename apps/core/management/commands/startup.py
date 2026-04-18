import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.system_ui import read_startup_report


def _read_timing_lock(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _phase_timings(payload: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    phases = payload.get("phase_timings")
    if not isinstance(phases, list):
        return []
    return [phase for phase in phases if isinstance(phase, dict)]


def _format_duration_seconds(value: object) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "unknown"
    return f"{seconds:.3f}s"


def _format_duration_ms(value: object) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return "unknown"
    return f"{milliseconds / 1000:.3f}s"


class Command(BaseCommand):
    """Display startup activity captured by suite lifecycle scripts."""

    help = "Show recent start.sh and upgrade.sh lifecycle entries from the startup report log."

    def add_arguments(self, parser):
        parser.add_argument(
            "--n",
            type=int,
            dest="limit",
            default=10,
            help=(
                "Number of entries to display from the startup report log "
                "(default: 10)."
            ),
        )
        parser.add_argument(
            "--timings",
            action="store_true",
            help="Show the most recent startup timing breakdown from lock files.",
        )

    def handle(self, *args, **options):  # noqa: D401 - inherited docstring
        if options.get("timings"):
            self._handle_timings()
            return

        limit = options["limit"]
        if limit < 1:
            limit = 10

        report = read_startup_report(limit=limit, base_dir=Path(settings.BASE_DIR))
        log_path = report.get("log_path")
        if log_path:
            self.stdout.write(f"Startup report log: {log_path}")

        clock_warning = report.get("clock_warning")
        if clock_warning:
            self.stderr.write(str(clock_warning))

        error = report.get("error")
        if error:
            self.stderr.write(str(error))
            return

        entries = report.get("entries", [])
        if not entries:
            self.stdout.write("No startup activity has been recorded yet.")
            return

        for entry in entries:
            timestamp = entry.get("timestamp_label") or entry.get("timestamp_raw") or ""
            script = entry.get("script") or "unknown"
            event = entry.get("event") or "event"
            detail = entry.get("detail") or ""

            message = f"{timestamp} [{script}] {event}"
            if detail:
                message = f"{message} — {detail}"
            self.stdout.write(message)

    def _handle_timings(self) -> None:
        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        startup_payload = _read_timing_lock(lock_dir / "startup_duration.lck")
        orchestration_payload = _read_timing_lock(lock_dir / "startup_orchestrate_status.lck")
        if not startup_payload and not orchestration_payload:
            self.stdout.write("No startup timing data has been recorded yet.")
            return

        if startup_payload:
            status_value = "ok" if int(startup_payload.get("status") or 0) == 0 else "error"
            self.stdout.write("Startup timing summary:")
            self.stdout.write(
                f"  Measured readiness window: {_format_duration_seconds(startup_payload.get('duration_seconds'))}"
            )
            self.stdout.write(f"  Status: {status_value}")
            self.stdout.write(f"  Started at: {startup_payload.get('started_at') or 'unknown'}")
            self.stdout.write(f"  Finished at: {startup_payload.get('finished_at') or 'unknown'}")
            if startup_payload.get("port"):
                self.stdout.write(f"  Port: {startup_payload.get('port')}")

            phase_timings = _phase_timings(startup_payload)
            if phase_timings:
                self.stdout.write("")
                self.stdout.write("Service-start phases:")
                for phase in phase_timings:
                    message = (
                        f"  - {phase.get('name') or 'phase'}: "
                        f"{_format_duration_ms(phase.get('duration_ms'))} "
                        f"[{phase.get('status') or 'unknown'}]"
                    )
                    self.stdout.write(message)

        orchestration_phases = _phase_timings(orchestration_payload)
        if orchestration_payload:
            self.stdout.write("")
            self.stdout.write("Orchestration phase:")
            self.stdout.write(
                f"  Total: {_format_duration_seconds(orchestration_payload.get('duration_seconds'))}"
            )
            self.stdout.write(
                f"  Started at: {orchestration_payload.get('started_at') or 'unknown'}"
            )
            self.stdout.write(
                f"  Finished at: {orchestration_payload.get('finished_at') or 'unknown'}"
            )
            if orchestration_phases:
                self.stdout.write("  Steps:")
                for phase in orchestration_phases:
                    detail = phase.get("detail") or ""
                    message = (
                        f"    - {phase.get('name') or 'phase'}: "
                        f"{_format_duration_ms(phase.get('duration_ms'))} "
                        f"[{phase.get('status') or 'unknown'}]"
                    )
                    if detail:
                        message = f"{message} — {detail}"
                    self.stdout.write(message)
