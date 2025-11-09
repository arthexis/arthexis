import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.core.management import call_command


class FakeProcess:
    def __init__(
        self,
        pid: int,
        name: str,
        cmdline: list[str],
        cpu_values: list[float],
        rss_values: list[int],
        read_values: list[int],
        write_values: list[int],
    ) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "cmdline": cmdline,
            "cwd": "/workspace/arthexis",
            "exe": "/workspace/arthexis/manage.py",
        }
        self._cpu_values = list(cpu_values)
        self._rss_values = list(rss_values)
        self._read_values = list(read_values)
        self._write_values = list(write_values)

    def cpu_percent(self, interval=None):
        return self._cpu_values.pop(0)

    def memory_info(self):
        return SimpleNamespace(rss=self._rss_values.pop(0))

    def io_counters(self):
        return SimpleNamespace(
            read_bytes=self._read_values.pop(0),
            write_bytes=self._write_values.pop(0),
        )

    def cmdline(self):
        return list(self.info["cmdline"])

    def name(self):
        return self.info["name"]

    def exe(self):
        return self.info["exe"]


def _build_psutil_stub(processes):
    cpu_sequence = [0.0, 55.0, 0.0, 60.0]
    virtual_memory_samples = [
        SimpleNamespace(percent=40.0, used=4 * 1024**3, total=8 * 1024**3),
        SimpleNamespace(percent=41.0, used=int(4.1 * 1024**3), total=8 * 1024**3),
    ]
    swap_samples = [
        SimpleNamespace(percent=10.0, used=256 * 1024**2, total=2 * 1024**3),
        SimpleNamespace(percent=12.0, used=300 * 1024**2, total=2 * 1024**3),
    ]

    return SimpleNamespace(
        process_iter=MagicMock(side_effect=[processes, processes]),
        cpu_percent=MagicMock(side_effect=cpu_sequence),
        virtual_memory=MagicMock(side_effect=virtual_memory_samples),
        swap_memory=MagicMock(side_effect=swap_samples),
        NoSuchProcess=RuntimeError,
        AccessDenied=RuntimeError,
        ZombieProcess=RuntimeError,
    )


def _time_mocks():
    current_time = {"value": 0.0}

    def fake_monotonic():
        return current_time["value"]

    def fake_sleep(seconds):
        current_time["value"] += seconds

    return fake_monotonic, fake_sleep


def _build_processes():
    process_a = FakeProcess(
        pid=101,
        name="python",
        cmdline=["python", "manage.py", "runserver"],
        cpu_values=[0.0, 12.0, 0.0, 10.0],
        rss_values=[200 * 1024**2, 210 * 1024**2],
        read_values=[1_000, 1_500],
        write_values=[2_000, 2_600],
    )
    process_b = FakeProcess(
        pid=202,
        name="celery",
        cmdline=["celery", "-A", "config", "worker", "--concurrency=2"],
        cpu_values=[0.0, 5.0, 0.0, 7.0],
        rss_values=[150 * 1024**2, 160 * 1024**2],
        read_values=[500, 700],
        write_values=[800, 820],
    )
    return [process_a, process_b]


def test_benchmark_command_outputs_summary():
    processes = _build_processes()
    psutil_stub = _build_psutil_stub(processes)
    fake_monotonic, fake_sleep = _time_mocks()

    buffer = io.StringIO()

    with patch("core.management.commands.benchmark.psutil", psutil_stub), patch(
        "core.management.commands.benchmark.time.monotonic", fake_monotonic
    ), patch("core.management.commands.benchmark.time.sleep", fake_sleep):
        call_command(
            "benchmark",
            "--duration",
            "2",
            "--interval",
            "1",
            stdout=buffer,
        )

    output = buffer.getvalue()
    assert "Benchmark summary:" in output
    assert "System CPU usage: avg 57.5%" in output
    assert "Arthexis CPU usage: avg 17.0%" in output
    assert "Arthexis memory usage: avg 360.0 MiB" in output
    assert "Arthexis disk I/O: read 700 B, written 620 B" in output
    assert "PID 101" in output
    assert "PID 202" in output
    assert "cmd: python manage.py runserver" in output


def test_benchmark_command_can_emit_json():
    processes = _build_processes()
    psutil_stub = _build_psutil_stub(processes)
    fake_monotonic, fake_sleep = _time_mocks()

    buffer = io.StringIO()

    with patch("core.management.commands.benchmark.psutil", psutil_stub), patch(
        "core.management.commands.benchmark.time.monotonic", fake_monotonic
    ), patch("core.management.commands.benchmark.time.sleep", fake_sleep):
        call_command(
            "benchmark",
            "--duration",
            "2",
            "--interval",
            "1",
            "--json",
            stdout=buffer,
        )

    data = buffer.getvalue()
    result = json.loads(data)
    assert result["samples"] == 2
    assert result["suite"]["cpu"]["average"] == 17.0
    assert result["suite"]["memory"]["average_bytes"] == 377487360.0
    assert len(result["suite"]["processes"]) == 2
