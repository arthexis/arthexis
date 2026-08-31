from __future__ import annotations

from tests.plugins.benchmark_capture import BenchmarkRecord, build_benchmark_payload


def test_benchmark_payload_groups_counts_and_slowest_tests():
    payload = build_benchmark_payload(
        [
            BenchmarkRecord(
                nodeid="apps/core/tests/test_fast.py::test_fast",
                duration_seconds=0.25,
                file_path="apps/core/tests/test_fast.py",
                phase_durations={"call": 0.25},
                status="passed",
                test_name="test_fast",
            ),
            BenchmarkRecord(
                nodeid="apps/sites/tests/test_slow.py::test_slow",
                duration_seconds=1.5,
                file_path="apps/sites/tests/test_slow.py",
                phase_durations={"call": 1.5},
                status="failed",
                test_name="test_slow",
            ),
        ],
        duration_seconds=2.0,
        exitstatus=1,
        generated_at="2026-05-20T12:00:02+00:00",
        started_at="2026-05-20T12:00:00+00:00",
        top_limit=1,
    )

    assert payload["schema_version"] == 1
    assert payload["summary"] == {
        "duration_seconds": 2.0,
        "test_count": 2,
        "status_counts": {"failed": 1, "passed": 1},
    }
    assert payload["slowest_tests"][0]["nodeid"] == "apps/sites/tests/test_slow.py::test_slow"
    assert payload["groups"][0]["name"] == "apps.sites"
    assert payload["groups"][0]["duration_seconds"] == 1.5


class _ConfigWithoutWorkerInput:
    pass


class _ConfigWithWorkerInput:
    workerinput = {"workerid": "gw0"}


def test_is_xdist_worker_false_in_controller_or_non_xdist_process():
    from tests.plugins.benchmark_capture import _is_xdist_worker

    assert _is_xdist_worker(_ConfigWithoutWorkerInput()) is False


def test_is_xdist_worker_true_in_xdist_worker_process():
    from tests.plugins.benchmark_capture import _is_xdist_worker

    assert _is_xdist_worker(_ConfigWithWorkerInput()) is True
