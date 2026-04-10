from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

try:
    import psutil
except ImportError:  # pragma: no cover - handled in command execution
    psutil = None  # type: ignore[assignment]


DEFAULT_DEVICE_MEMORY_GB = (1, 2, 3, 4)
DEFAULT_VERSIONS = ("1.6J", "2.0.1", "2.1")


@dataclass
class BenchmarkRun:
    device_memory_gb: int
    duration_seconds: float
    peak_rss_bytes: int
    rc: int
    stderr: str
    stdout: str
    version: str

    @property
    def device_memory_bytes(self) -> int:
        return self.device_memory_gb * 1024**3

    @property
    def peak_utilization_percent(self) -> float:
        if self.device_memory_bytes <= 0:
            return 0.0
        return (self.peak_rss_bytes / self.device_memory_bytes) * 100.0

    @property
    def within_budget(self) -> bool:
        return self.peak_rss_bytes <= self.device_memory_bytes

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "device_memory_gb": self.device_memory_gb,
            "device_memory_bytes": self.device_memory_bytes,
            "duration_seconds": self.duration_seconds,
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_utilization_percent": self.peak_utilization_percent,
            "within_budget": self.within_budget,
            "return_code": self.rc,
        }


class Command(BaseCommand):
    help = "Benchmark OCPP feature operations under assumed maximum device memory profiles."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--memory-gb",
            nargs="+",
            type=int,
            default=list(DEFAULT_DEVICE_MEMORY_GB),
            help="Device total memory profiles in GiB (default: 1 2 3 4).",
        )
        parser.add_argument(
            "--versions",
            nargs="+",
            default=list(DEFAULT_VERSIONS),
            choices=("1.6J", "1.6", "2.0.1", "2.1"),
            help="OCPP coverage versions to benchmark (default: 1.6J 2.0.1 2.1).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON summary output.",
        )

    def handle(self, *args, **options):
        if psutil is None:
            raise CommandError(
                "The 'psutil' package is required to run this benchmark."
            )

        memory_profiles = sorted(set(options["memory_gb"]))
        if any(value <= 0 for value in memory_profiles):
            raise CommandError("--memory-gb values must be greater than zero.")

        versions = [
            "1.6J" if version == "1.6" else version for version in options["versions"]
        ]

        results: list[BenchmarkRun] = []
        for version in versions:
            for memory_gb in memory_profiles:
                result = self._run_single(version=version, memory_gb=memory_gb)
                results.append(result)

        payload = {
            "profiles_gb": memory_profiles,
            "versions": versions,
            "runs": [result.to_dict() for result in results],
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2))
            return

        self.stdout.write("OCPP memory profile benchmark summary:")
        for result in results:
            budget_state = "PASS" if result.within_budget and result.rc == 0 else "FAIL"
            self.stdout.write(
                "  "
                f"version {result.version} @ {result.device_memory_gb} GiB: "
                f"duration {result.duration_seconds:.2f}s, "
                f"peak RSS {self._format_bytes(result.peak_rss_bytes)} "
                f"({result.peak_utilization_percent:.1f}% of device memory) [{budget_state}]"
            )
            if result.rc != 0 and result.stderr:
                self.stdout.write(
                    self.style.WARNING(f"    stderr: {result.stderr.strip()}")
                )

    def _run_single(self, *, version: str, memory_gb: int) -> BenchmarkRun:
        repo_root = Path(settings.BASE_DIR)
        manage_py = repo_root / "manage.py"

        with (
            tempfile.NamedTemporaryFile(suffix=".json") as json_file,
            tempfile.NamedTemporaryFile(suffix=".svg") as badge_file,
        ):
            command = [
                sys.executable,
                str(manage_py),
                "ocpp",
                "coverage",
                "--version",
                version,
                "--json-path",
                json_file.name,
                "--badge-path",
                badge_file.name,
            ]

            start = time.monotonic()
            process = subprocess.Popen(
                command,
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            process_info = psutil.Process(process.pid)
            peak_rss_bytes = 0

            while True:
                if process.poll() is not None:
                    break
                try:
                    rss = process_info.memory_info().rss
                except (
                    psutil.AccessDenied,
                    psutil.NoSuchProcess,
                    psutil.ZombieProcess,
                ):
                    rss = 0
                peak_rss_bytes = max(peak_rss_bytes, int(rss))
                time.sleep(0.1)

            stdout, stderr = process.communicate()
            duration_seconds = time.monotonic() - start

            try:
                rss = process_info.memory_info().rss
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                rss = 0
            peak_rss_bytes = max(peak_rss_bytes, int(rss))

            return BenchmarkRun(
                version=version,
                device_memory_gb=memory_gb,
                duration_seconds=duration_seconds,
                peak_rss_bytes=peak_rss_bytes,
                rc=process.returncode,
                stderr=stderr,
                stdout=stdout,
            )

    def _format_bytes(self, value: int) -> str:
        if value <= 0:
            return "0 B"
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        size = float(value)
        index = 0
        while size >= 1024 and index < len(units) - 1:
            size /= 1024
            index += 1
        if units[index] == "B":
            return f"{int(size)} {units[index]}"
        return f"{size:.1f} {units[index]}"
