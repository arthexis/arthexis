import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.management.commands import benchmark_ocpp_memory
from apps.ocpp.management.commands.benchmark_ocpp_memory import BenchmarkRun, Command


@pytest.mark.django_db(databases=[])
def test_benchmark_ocpp_memory_json_output(monkeypatch):
    run = BenchmarkRun(
        version="1.6J",
        device_memory_gb=1,
        duration_seconds=1.25,
        peak_rss_bytes=256 * 1024**2,
        rc=0,
        stderr="",
        stdout="ok",
    )

    def fake_run_single(self, *, version, memory_gb):
        assert version == "1.6J"
        assert memory_gb == 1
        return run

    monkeypatch.setattr(Command, "_run_single", fake_run_single)

    stdout = io.StringIO()
    call_command(
        "benchmark_ocpp_memory",
        "--memory-gb",
        "1",
        "--versions",
        "1.6J",
        "--json",
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert '"version": "1.6J"' in output
    assert '"within_budget": true' in output


@pytest.mark.django_db(databases=[])
def test_benchmark_ocpp_memory_rejects_non_positive_profile():
    with pytest.raises(CommandError):
        call_command("benchmark_ocpp_memory", "--memory-gb", "0")


@pytest.mark.django_db(databases=[])
def test_benchmark_run_budget_helpers():
    run = BenchmarkRun(
        version="2.1",
        device_memory_gb=2,
        duration_seconds=2.0,
        peak_rss_bytes=1024**3,
        rc=0,
        stderr="",
        stdout="",
    )

    assert run.device_memory_bytes == 2 * 1024**3
    assert round(run.peak_utilization_percent, 1) == 50.0
    assert run.within_budget


@pytest.mark.django_db(databases=[])
def test_command_requires_psutil(monkeypatch):
    monkeypatch.setattr(benchmark_ocpp_memory, "psutil", None)

    with pytest.raises(CommandError):
        call_command("benchmark_ocpp_memory")
