from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

try:
    import psutil
except ImportError as exc:  # pragma: no cover - handled via CommandError in handle
    psutil = None  # type: ignore[assignment]


def _format_bytes(value: float) -> str:
    """Return ``value`` formatted using a human friendly unit."""

    if value <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if units[index] == "B":
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def _format_percent(value: float) -> str:
    return f"{value:.1f}%"


def _average(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return statistics.fmean(values_list)


def _normalize_path_text(value: str) -> str:
    """Normalize a path-like string for comparison across platforms."""

    return str(value).replace("\\", "/").lower()


@dataclass
class _ProcessStats:
    pid: int
    name: str
    command: str
    cpu_total: float = 0.0
    cpu_peak: float = 0.0
    memory_total: int = 0
    memory_peak: int = 0
    samples: int = 0
    io_read: int = 0
    io_write: int = 0
    _last_io: Tuple[int, int] | None = field(default=None, repr=False)

    def update(self, cpu: float, memory: int, io_counters) -> None:
        self.samples += 1
        self.cpu_total += cpu
        self.cpu_peak = max(self.cpu_peak, cpu)
        self.memory_total += memory
        self.memory_peak = max(self.memory_peak, memory)
        if io_counters is not None:
            read_bytes = getattr(io_counters, "read_bytes", 0)
            write_bytes = getattr(io_counters, "write_bytes", 0)
            current = (int(read_bytes), int(write_bytes))
            if self._last_io is not None:
                read_delta = max(0, current[0] - self._last_io[0])
                write_delta = max(0, current[1] - self._last_io[1])
                self.io_read += read_delta
                self.io_write += write_delta
            self._last_io = current

    @property
    def avg_cpu(self) -> float:
        if not self.samples:
            return 0.0
        return self.cpu_total / self.samples

    @property
    def avg_memory(self) -> float:
        if not self.samples:
            return 0.0
        return self.memory_total / self.samples

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "command": self.command,
            "samples": self.samples,
            "avg_cpu": self.avg_cpu,
            "max_cpu": self.cpu_peak,
            "avg_memory_bytes": self.avg_memory,
            "max_memory_bytes": self.memory_peak,
            "io": {"read_bytes": self.io_read, "write_bytes": self.io_write},
        }


def _normalize_command(proc, info: dict) -> str:
    cmdline = info.get("cmdline") or []
    if cmdline:
        return " ".join(str(part) for part in cmdline)
    try:
        return " ".join(proc.cmdline())
    except Exception:  # pragma: no cover - defensive fallback
        return info.get("name") or f"pid {info.get('pid')}"


def _process_name(proc, info: dict) -> str:
    name = info.get("name")
    if name:
        return str(name)
    try:
        return proc.name()
    except Exception:  # pragma: no cover - defensive fallback
        return f"pid {info.get('pid')}"


def _collect_processes(base_dir: Path) -> Dict[int, Tuple[psutil.Process, dict]]:
    results: Dict[int, Tuple[psutil.Process, dict]] = {}
    base_dir_lower = str(base_dir).lower()
    normalized_base_dir = _normalize_path_text(base_dir_lower)
    normalized_base_dir_no_drive = normalized_base_dir.split(":", 1)[-1]
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd", "exe"]):
        if proc.pid == os.getpid():
            continue
        try:
            info = proc.info
        except Exception:  # pragma: no cover - defensive fallback
            continue
        cmdline = " ".join(str(part) for part in info.get("cmdline") or [])
        cwd = info.get("cwd") or ""
        exe = info.get("exe") or ""
        combined = cmdline.lower()
        normalized_cmdline = _normalize_path_text(cmdline)
        normalized_cwd = _normalize_path_text(cwd)
        normalized_exe = _normalize_path_text(exe)
        try:
            if (
                "arthexis" in combined
                or base_dir_lower in combined
                or normalized_base_dir in normalized_cmdline
                or normalized_base_dir_no_drive in normalized_cmdline
                or (cwd and base_dir_lower in cwd.lower())
                or (cwd and normalized_base_dir in normalized_cwd)
                or (cwd and normalized_base_dir_no_drive in normalized_cwd)
                or (exe and base_dir_lower in exe.lower())
                or (exe and normalized_base_dir in normalized_exe)
                or (exe and normalized_base_dir_no_drive in normalized_exe)
            ):
                results[proc.pid] = (proc, info)
        except Exception:  # pragma: no cover - defensive fallback
            continue
    return results


class Command(BaseCommand):
    """Measure the estimated system resources consumed by the suite."""

    help = "Measure the estimated CPU, memory, and disk usage of the running Arthexis suite."

    def add_arguments(self, parser):
        parser.add_argument(
            "--duration",
            type=float,
            default=30.0,
            help="How many seconds to sample for (default: 30).",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=1.0,
            help="Sampling interval in seconds (default: 1).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the collected measurements as JSON instead of formatted text.",
        )

    def handle(self, *args, **options):
        if psutil is None:
            raise CommandError("The 'psutil' package is required to run this command.")

        duration = float(options["duration"])
        interval = float(options["interval"])
        as_json = bool(options["json"])

        if duration <= 0:
            raise CommandError("--duration must be greater than zero.")
        if interval <= 0:
            raise CommandError("--interval must be greater than zero.")

        base_dir = Path(settings.BASE_DIR).resolve()
        if not as_json:
            self.stdout.write(
                f"Sampling resource usage for {duration:.1f} seconds "
                f"(interval {interval:.1f} seconds). Press Ctrl+C to stop early."
            )

        stats_map: Dict[int, _ProcessStats] = {}
        suite_cpu_samples: list[float] = []
        suite_memory_samples: list[int] = []
        system_cpu_samples: list[float] = []
        system_memory_percent_samples: list[float] = []
        system_memory_used_samples: list[int] = []
        swap_percent_samples: list[float] = []
        swap_used_samples: list[int] = []

        start_time = time.monotonic()
        end_time = start_time + duration
        interrupted = False
        memory_total = 0
        swap_total = 0

        try:
            while True:
                now = time.monotonic()
                if now >= end_time:
                    break

                processes = _collect_processes(base_dir)

                for pid, (proc, info) in processes.items():
                    if pid not in stats_map:
                        stats_map[pid] = _ProcessStats(
                            pid=pid,
                            name=_process_name(proc, info),
                            command=_normalize_command(proc, info),
                        )
                    try:
                        proc.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue

                psutil.cpu_percent(interval=None)

                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    break
                sleep_for = min(interval, remaining)
                time.sleep(sleep_for)

                total_cpu = psutil.cpu_percent(interval=None)
                mem_info = psutil.virtual_memory()
                swap_info = psutil.swap_memory()

                memory_total = getattr(mem_info, "total", memory_total)
                swap_total = getattr(swap_info, "total", swap_total)

                system_cpu_samples.append(float(total_cpu))
                system_memory_percent_samples.append(float(getattr(mem_info, "percent", 0.0)))
                system_memory_used_samples.append(int(getattr(mem_info, "used", 0)))
                swap_percent_samples.append(float(getattr(swap_info, "percent", 0.0)))
                swap_used_samples.append(int(getattr(swap_info, "used", 0)))

                suite_cpu = 0.0
                suite_memory = 0

                for pid, (proc, info) in processes.items():
                    stats = stats_map.get(pid)
                    if stats is None:
                        continue
                    try:
                        cpu_value = proc.cpu_percent(interval=None)
                        memory_info = proc.memory_info()
                        io_counters = None
                        try:
                            io_counters = proc.io_counters()
                        except (psutil.AccessDenied, AttributeError):
                            io_counters = None

                        suite_cpu += float(cpu_value)
                        suite_memory += int(getattr(memory_info, "rss", 0))

                        stats.update(float(cpu_value), int(getattr(memory_info, "rss", 0)), io_counters)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue

                suite_cpu_samples.append(suite_cpu)
                suite_memory_samples.append(suite_memory)

        except KeyboardInterrupt:
            interrupted = True

        elapsed = max(0.0, time.monotonic() - start_time)
        sample_count = len(system_cpu_samples)

        suite_cpu_average = sum(stats.avg_cpu for stats in stats_map.values() if stats.samples)
        suite_memory_average = sum(
            stats.avg_memory for stats in stats_map.values() if stats.samples
        )

        suite_io_read = sum(stats.io_read for stats in stats_map.values())
        suite_io_write = sum(stats.io_write for stats in stats_map.values())

        summary = {
            "duration_seconds": elapsed,
            "requested_duration": duration,
            "interval_seconds": interval,
            "samples": sample_count,
            "interrupted": interrupted,
            "system": {
                "cpu": {
                    "average": _average(system_cpu_samples),
                    "min": min(system_cpu_samples) if system_cpu_samples else 0.0,
                    "max": max(system_cpu_samples) if system_cpu_samples else 0.0,
                },
                "memory": {
                    "average_percent": _average(system_memory_percent_samples),
                    "max_percent": max(system_memory_percent_samples)
                    if system_memory_percent_samples
                    else 0.0,
                    "average_used_bytes": _average(system_memory_used_samples),
                    "max_used_bytes": max(system_memory_used_samples)
                    if system_memory_used_samples
                    else 0,
                    "total_bytes": memory_total,
                },
                "swap": {
                    "average_percent": _average(swap_percent_samples),
                    "max_percent": max(swap_percent_samples) if swap_percent_samples else 0.0,
                    "average_used_bytes": _average(swap_used_samples),
                    "max_used_bytes": max(swap_used_samples) if swap_used_samples else 0,
                    "total_bytes": swap_total,
                },
            },
            "suite": {
                "cpu": {
                    "average": suite_cpu_average,
                    "max": max(suite_cpu_samples) if suite_cpu_samples else 0.0,
                },
                "memory": {
                    "average_bytes": suite_memory_average,
                    "max_bytes": max(suite_memory_samples) if suite_memory_samples else 0,
                },
                "io": {
                    "read_bytes": suite_io_read,
                    "write_bytes": suite_io_write,
                },
                "processes": [stats.to_dict() for stats in stats_map.values() if stats.samples],
            },
        }

        if as_json:
            self.stdout.write(json.dumps(summary, indent=2))
            return

        if interrupted:
            self.stdout.write(self.style.WARNING("Sampling interrupted early; partial results follow."))

        if sample_count == 0:
            self.stdout.write(
                self.style.WARNING(
                    "No samples were collected. Ensure the suite is running and increase the duration."
                )
            )
            return

        self.stdout.write("")
        self.stdout.write("Benchmark summary:")
        self.stdout.write(
            f"  Duration observed: {summary['duration_seconds']:.1f}s across {sample_count} samples"
        )

        sys_cpu = summary["system"]["cpu"]
        self.stdout.write(
            "  System CPU usage: "
            f"avg {_format_percent(sys_cpu['average'])}, "
            f"min {_format_percent(sys_cpu['min'])}, "
            f"max {_format_percent(sys_cpu['max'])}"
        )

        sys_mem = summary["system"]["memory"]
        self.stdout.write(
            "  System memory usage: "
            f"avg {_format_percent(sys_mem['average_percent'])}, "
            f"max {_format_percent(sys_mem['max_percent'])}, "
            f"avg used {_format_bytes(sys_mem['average_used_bytes'])}"
        )

        swap_mem = summary["system"]["swap"]
        if swap_mem["total_bytes"]:
            self.stdout.write(
                "  Swap usage: "
                f"avg {_format_percent(swap_mem['average_percent'])}, "
                f"max {_format_percent(swap_mem['max_percent'])}"
            )

        suite_cpu = summary["suite"]["cpu"]
        self.stdout.write(
            "  Arthexis CPU usage: "
            f"avg {_format_percent(suite_cpu['average'])}, "
            f"peak {_format_percent(suite_cpu['max'])}"
        )

        suite_mem = summary["suite"]["memory"]
        self.stdout.write(
            "  Arthexis memory usage: "
            f"avg {_format_bytes(suite_mem['average_bytes'])}, "
            f"peak {_format_bytes(suite_mem['max_bytes'])}"
        )

        suite_io = summary["suite"]["io"]
        self.stdout.write(
            "  Arthexis disk I/O: "
            f"read {_format_bytes(suite_io['read_bytes'])}, "
            f"written {_format_bytes(suite_io['write_bytes'])}"
        )

        process_summaries = [stats for stats in stats_map.values() if stats.samples]
        if not process_summaries:
            self.stdout.write(
                self.style.WARNING(
                    "No Arthexis processes were detected during sampling."
                )
            )
            return

        self.stdout.write("")
        self.stdout.write("Observed processes:")
        for stats in sorted(process_summaries, key=lambda item: item.avg_cpu, reverse=True):
            self.stdout.write(
                f"  PID {stats.pid} {stats.name}: "
                f"avg CPU {_format_percent(stats.avg_cpu)}, "
                f"peak CPU {_format_percent(stats.cpu_peak)}, "
                f"avg RSS {_format_bytes(stats.avg_memory)}, "
                f"peak RSS {_format_bytes(stats.memory_peak)}, "
                f"I/O read {_format_bytes(stats.io_read)}, write {_format_bytes(stats.io_write)}"
            )
            if stats.command and stats.command != stats.name:
                display = stats.command
                if len(display) > 120:
                    display = display[:117] + "..."
                self.stdout.write(f"    cmd: {display}")
