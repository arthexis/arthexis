from __future__ import annotations

import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.screens.lcd import (
    LCDTimings,
    LCDUnavailableError,
    prepare_lcd_controller,
)
from apps.screens.lcd_screen import LOG_FILE, WORK_FILE
from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LEGACY_FEATURE_LOCK,
    LCD_LOW_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
)

if os.name == "nt":
    resource = None
else:
    import resource


DEFAULT_OUTFILE = Path("work") / "lcd-debug.txt"


class Command(BaseCommand):
    """Generate a detailed LCD service debugging report."""

    help = "Generate a detailed LCD service debugging report"

    def __init__(self):
        super().__init__()
        self._started_at = time.monotonic()

    def add_arguments(self, parser):
        parser.add_argument(
            "--long",
            action="store_true",
            dest="long_wait",
            help="Wait 30 seconds to collect an additional sample",
        )
        parser.add_argument(
            "--double",
            action="store_true",
            help="Wait 60 seconds to collect two additional samples",
        )
        parser.add_argument(
            "--outfile",
            type=str,
            help="Path to write the debugging report (defaults to work/lcd-debug.txt)",
        )

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        waits = self._wait_schedule(options)
        outfile = self._resolve_outfile(options, base_dir)

        report_lines: list[str] = []
        report_lines.extend(self._metadata_section(base_dir))
        report_lines.append("")
        report_lines.extend(self._lcd_timing_section())
        report_lines.append("")

        elapsed = 0
        samples = 0
        report_lines.append("Samples:")
        report_lines.append("--------")
        report_lines.extend(self._sample(label="Initial sample", base_dir=base_dir, elapsed=elapsed))
        samples += 1

        for wait in waits:
            self.stdout.write(f"Waiting {wait} seconds to capture additional sample...")
            self._sleep(wait)
            elapsed += wait
            samples += 1
            report_lines.append("")
            report_lines.extend(
                self._sample(label=f"After {elapsed}s", base_dir=base_dir, elapsed=elapsed)
            )

        report_lines.append("")
        report_lines.extend(self._environment_section())
        report_lines.append("")
        report_lines.extend(self._lcd_probe_section(base_dir))
        report_lines.append("")
        report_lines.extend(self._encoding_health_section(base_dir))

        report = "\n".join(report_lines)
        outfile.parent.mkdir(parents=True, exist_ok=True)
        outfile.write_text(report, encoding="utf-8")

        self.stdout.write(report)
        self.stdout.write(self.style.SUCCESS(f"Saved LCD debug report to {outfile}"))
        self.stdout.write(
            f"Samples collected: {samples}; total runtime: {time.monotonic() - self._started_at:.1f}s"
        )

    # ------------------------------------------------------------------
    def _wait_schedule(self, options: dict) -> list[int]:
        if options.get("double"):
            return [30, 30]
        if options.get("long_wait"):
            return [30]
        return []

    def _resolve_outfile(self, options: dict, base_dir: Path) -> Path:
        outfile = options.get("outfile")
        if outfile:
            return Path(outfile)
        return base_dir / DEFAULT_OUTFILE

    def _sample(self, *, label: str, base_dir: Path, elapsed: int) -> list[str]:
        now = datetime.now(tz=timezone.utc)
        lines: list[str] = []
        lines.append(f"{label} (T+{elapsed}s)")
        lines.append("~~~~~~~~~~~~~~~~~~~~~~~~")
        lines.append(f"Captured at: {now.isoformat()}")
        lines.append(f"Command runtime: {time.monotonic() - self._started_at:.1f}s")
        lines.append("")
        lines.extend(self._lockfile_section(base_dir))
        lines.append("")
        lines.extend(self._runtime_file_section(base_dir))
        lines.append("")
        lines.extend(self._memory_section())
        return lines

    def _metadata_section(self, base_dir: Path) -> list[str]:
        return [
            "LCD Debug Report",
            "================",
            f"Base directory: {base_dir}",
            f"PID: {os.getpid()}",
            f"Python: {platform.python_version()} ({platform.platform()})",
            f"Started at: {datetime.now(tz=timezone.utc).isoformat()}",
        ]

    def _lcd_timing_section(self) -> list[str]:
        timings = LCDTimings.from_env()
        return [
            "LCD timings:",
            f"- pulse_enable_delay: {timings.pulse_enable_delay:.4f}s (LCD_PULSE_ENABLE_DELAY={os.getenv('LCD_PULSE_ENABLE_DELAY', 'default')})",
            f"- pulse_disable_delay: {timings.pulse_disable_delay:.4f}s (LCD_PULSE_DISABLE_DELAY={os.getenv('LCD_PULSE_DISABLE_DELAY', 'default')})",
            f"- command_delay: {timings.command_delay:.4f}s (LCD_COMMAND_DELAY={os.getenv('LCD_COMMAND_DELAY', 'default')})",
            f"- data_delay: {timings.data_delay:.4f}s (LCD_DATA_DELAY={os.getenv('LCD_DATA_DELAY', 'default')})",
            f"- clear_delay: {timings.clear_delay:.4f}s (LCD_CLEAR_DELAY={os.getenv('LCD_CLEAR_DELAY', 'default')})",
        ]

    def _lockfile_section(self, base_dir: Path) -> list[str]:
        lock_dir = base_dir / ".locks"
        names = [
            LCD_HIGH_LOCK_FILE,
            LCD_LOW_LOCK_FILE,
            LCD_LEGACY_FEATURE_LOCK,
            LCD_RUNTIME_LOCK_FILE,
            "service.lck",
        ]
        discovered = sorted({p.name for p in lock_dir.glob("*lcd*")}) if lock_dir.exists() else []
        for name in discovered:
            if name not in names:
                names.append(name)

        lines = ["Lockfiles:", f"- Directory: {lock_dir}"]
        if not lock_dir.exists():
            lines.append("  (lock directory missing)")
            return lines

        for name in names:
            path = lock_dir / name
            lines.extend(self._describe_file(path, label=f"- {name}"))
        return lines

    def _runtime_file_section(self, base_dir: Path) -> list[str]:
        work_file = base_dir / "work" / WORK_FILE.name
        log_file = base_dir / "logs" / LOG_FILE.name
        lines = ["Runtime files:"]
        lines.extend(self._describe_file(work_file, label="- work/lcd-screen.txt"))
        lines.extend(self._describe_file(log_file, label="- logs/lcd-screen.log", max_lines=40))
        return lines

    def _memory_section(self) -> list[str]:
        if resource is None:
            return [
                "Memory and resource usage:",
                "- resource module unavailable on this platform",
            ]
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return [
            "Memory and resource usage:",
            f"- ru_maxrss: {usage.ru_maxrss}",
            f"- ru_ixrss: {usage.ru_ixrss}",
            f"- ru_idrss: {usage.ru_idrss}",
            f"- ru_isrss: {usage.ru_isrss}",
            f"- ru_minflt (page reclaims): {usage.ru_minflt}",
            f"- ru_majflt (page faults): {usage.ru_majflt}",
        ]

    def _environment_section(self) -> list[str]:
        keys = sorted(
            key
            for key in os.environ
            if any(prefix in key for prefix in ("LCD", "I2C", "DISPLAY", "ARTHEXIS"))
        )
        lines = ["Relevant environment variables:"]
        if not keys:
            lines.append("- none found")
            return lines

        for key in keys:
            lines.append(f"- {key}={os.environ.get(key, '')}")
        return lines

    def _describe_file(self, path: Path, *, label: str, max_lines: int = 15) -> list[str]:
        lines = [f"{label} -> {path}"]
        if not path.exists():
            lines.append("  missing")
            return lines

        try:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            lines.append(f"  size: {stat.st_size} bytes | modified: {modified}")
        except OSError as exc:  # pragma: no cover - rare filesystem errors
            lines.append(f"  (could not stat file: {exc})")
            return lines

        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            lines.append(f"  (could not read file: {exc})")
            return lines

        if not content:
            lines.append("  content: <empty>")
            return lines

        lines.append("  content preview:")
        for line in content[:max_lines]:
            lines.append(f"    {line}")
        if len(content) > max_lines:
            lines.append(f"    ... ({len(content) - max_lines} more lines)")
        return lines

    def _encoding_health_section(self, base_dir: Path) -> list[str]:
        lock_dir = base_dir / ".locks"
        work_file = base_dir / "work" / WORK_FILE.name
        lines = ["Encoding health checks:"]
        targets = [
            ("lcd-high", lock_dir / LCD_HIGH_LOCK_FILE),
            ("lcd-low", lock_dir / LCD_LOW_LOCK_FILE),
            ("lcd-screen", work_file),
        ]

        for label, path in targets:
            lines.extend(self._encoding_notes(path, label))
        return lines

    def _encoding_notes(self, path: Path, label: str) -> list[str]:
        lines = [f"- {label} -> {path}"]
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            lines.append("  missing")
            return lines
        except OSError as exc:
            lines.append(f"  (unable to read: {exc})")
            return lines

        if not raw:
            lines.append("  content: <empty>")
            return lines

        printable = {9, 10, 13} | set(range(32, 127))
        non_ascii = [f"0x{byte:02x}@{idx}" for idx, byte in enumerate(raw) if byte not in printable]
        preview = raw.decode("utf-8", errors="replace").splitlines()
        lines.append(f"  bytes: {len(raw)}")
        if non_ascii:
            lines.append(f"  non-printable bytes: {', '.join(non_ascii[:8])}")
            if len(non_ascii) > 8:
                lines.append(f"  ... {len(non_ascii) - 8} more occurrences")
        else:
            lines.append("  non-printable bytes: none detected")

        if preview:
            lines.append("  decoded preview:")
            for line in preview[:5]:
                lines.append(f"    {line}")
            if len(preview) > 5:
                lines.append(f"    ... ({len(preview) - 5} more lines)")
        return lines

    def _lcd_probe_section(self, base_dir: Path) -> list[str]:
        lock_dir = base_dir / ".locks"
        lines = ["LCD probe:"]
        if str(os.getenv("LCD_SKIP_PROBE", "")).lower() in {"1", "true", "yes", "on"}:
            lines.append("- skipped (LCD_SKIP_PROBE is set)")
            return lines
        try:
            lcd = prepare_lcd_controller(base_dir=base_dir)
        except LCDUnavailableError as exc:
            lines.append(f"- lcd init failed: {exc}")
            return lines
        except Exception as exc:  # pragma: no cover - hardware dependent
            lines.append(f"- lcd init failed: {exc}")
            return lines

        try:
            addresses = lcd.i2c_scan()
        except Exception as exc:  # pragma: no cover - requires hardware/tools
            lines.append(f"- i2c scan failed: {exc}")
        else:
            if addresses:
                lines.append(f"- detected I2C addresses: {', '.join(addresses)}")
            else:
                lines.append("- detected I2C addresses: none")

        for name in (LCD_HIGH_LOCK_FILE, LCD_LOW_LOCK_FILE):
            path = lock_dir / name
            if path.exists():
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                lines.append(f"- {name} mtime: {modified}")
        return lines

    def _sleep(self, seconds: int) -> None:
        time.sleep(seconds)
