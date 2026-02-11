from pathlib import Path

from apps.core.views.reports.release_publish.exceptions import PublishPending
from apps.core.views.reports.release_publish.services.pipeline import (
    StepDefinition,
    run_release_step,
)


def test_pipeline_step_ordering_and_progression():
    calls: list[str] = []
    persisted: list[int] = []
    ctx = {"started": True, "step": 0}

    def step_one(release, context, log_path, *, user=None):
        calls.append("one")

    def step_two(release, context, log_path, *, user=None):
        calls.append("two")

    steps = [StepDefinition("one", step_one), StepDefinition("two", step_two)]

    result = run_release_step(
        steps=steps,
        ctx=ctx,
        step_param="0",
        step_count=0,
        release=object(),
        log_path=Path("publish.log"),
        user=object(),
        append_log=lambda *_: None,
        persist_context=lambda c: persisted.append(c["step"]),
    )

    assert result.step_count == 1
    assert result.ctx["step"] == 1
    assert calls == ["one"]
    assert persisted == [1]


def test_pipeline_failure_propagation_sets_error_and_stops():
    ctx = {"started": True, "step": 0}
    messages: list[str] = []

    def failing_step(release, context, log_path, *, user=None):
        raise RuntimeError("boom")

    result = run_release_step(
        steps=[StepDefinition("failing", failing_step)],
        ctx=ctx,
        step_param="0",
        step_count=0,
        release=object(),
        log_path=Path("publish.log"),
        user=object(),
        append_log=lambda _p, msg: messages.append(msg),
        persist_context=lambda _c: None,
    )

    assert result.step_count == 0
    assert "error" in result.ctx
    assert any("failing failed" in msg for msg in messages)


def test_pipeline_pending_exception_does_not_set_error():
    ctx = {"started": True, "step": 0}

    def pending(release, context, log_path, *, user=None):
        raise PublishPending()

    result = run_release_step(
        steps=[StepDefinition("pending", pending)],
        ctx=ctx,
        step_param="0",
        step_count=0,
        release=object(),
        log_path=Path("publish.log"),
        user=object(),
        append_log=lambda *_: None,
        persist_context=lambda _c: None,
    )

    assert result.step_count == 0
    assert "error" not in result.ctx
