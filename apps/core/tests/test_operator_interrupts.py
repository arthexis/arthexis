from __future__ import annotations

import json

import pytest

from apps.core.services.operator_interrupts import (
    append_operator_interrupt_event,
    collect_manual_task_interrupts,
    drain_operator_interrupts,
    operator_interruptible_sleep,
    operator_local_feedback_lock_path,
)

pytestmark = pytest.mark.django_db


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def test_append_operator_interrupt_event_appends_existing_file(tmp_path) -> None:
    append_operator_interrupt_event({"source": "first"}, base_dir=tmp_path)
    append_operator_interrupt_event({"source": "second"}, base_dir=tmp_path)

    lines = (
        operator_local_feedback_lock_path(tmp_path)
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert [json.loads(line)["source"] for line in lines] == ["first", "second"]


def test_drain_operator_interrupts_reads_and_clears_jsonl_atomically(tmp_path) -> None:
    path = operator_local_feedback_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                json.dumps({"source": "first", "value": 1}),
                "not-json",
                json.dumps({"source": "second", "value": 2}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = drain_operator_interrupts(base_dir=tmp_path)

    assert result["drained"] is True
    assert [entry["source"] for entry in result["entries"]] == ["first", "second"]
    assert result["warnings"][0]["line"] == 2
    assert not path.exists()
    assert drain_operator_interrupts(base_dir=tmp_path)["entries"] == []


def test_operator_interruptible_sleep_short_wait_skips_feedback_check() -> None:
    clock = FakeClock()

    def fail_drain():
        raise AssertionError("short waits must not drain operator feedback")

    result = operator_interruptible_sleep(
        30,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        drain=fail_drain,
        manual_task_collector=lambda: [],
    )

    assert result["status"] == "completed"
    assert result["interrupted"] is False
    assert clock.sleeps == [30.0]


def test_operator_interruptible_sleep_long_wait_returns_feedback_interrupt() -> None:
    clock = FakeClock()

    result = operator_interruptible_sleep(
        120,
        context={"reason": "test-wait"},
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        drain=lambda: {
            "entries": [{"source": "user_story_local_feedback"}],
            "warnings": [],
        },
        manual_task_collector=lambda: [],
    )

    assert result["status"] == "interrupted_by_feedback"
    assert result["interrupted"] is True
    assert result["context"] == {"reason": "test-wait"}
    assert result["feedback"] == [{"source": "user_story_local_feedback"}]
    assert clock.sleeps == [30.0]


def test_operator_interruptible_sleep_polls_until_feedback_arrives() -> None:
    clock = FakeClock()
    drains = [
        {"entries": [], "warnings": []},
        {"entries": [{"source": "user_story_local_feedback", "id": 2}], "warnings": []},
    ]

    result = operator_interruptible_sleep(
        120,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        drain=lambda: drains.pop(0),
        manual_task_collector=lambda: [],
    )

    assert result["status"] == "interrupted_by_feedback"
    assert result["feedback"] == [{"source": "user_story_local_feedback", "id": 2}]
    assert clock.sleeps == [30.0, 30.0]


def test_operator_interruptible_sleep_long_wait_completes_without_feedback() -> None:
    clock = FakeClock()

    result = operator_interruptible_sleep(
        70,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        drain=lambda: {"entries": [], "warnings": []},
        manual_task_collector=lambda: [],
    )

    assert result["status"] == "completed"
    assert result["interrupted"] is False
    assert clock.sleeps == [30.0, 30.0, 10.0]


def test_collect_manual_task_interrupts_is_retired(tmp_path) -> None:
    assert collect_manual_task_interrupts(base_dir=tmp_path) == []
