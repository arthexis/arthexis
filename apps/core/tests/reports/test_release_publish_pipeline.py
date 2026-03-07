from pathlib import Path

from apps.core.views.reports.release_publish.exceptions import PublishPending
from apps.core.views.reports.release_publish.steps import StepDefinition, run_release_step


def test_pipeline_pending_exception_does_not_set_error_or_advance_step():
    ctx = {"started": True, "step": 0}
    persisted: list[dict] = []

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
        persist_context=lambda c: persisted.append(dict(c)),
    )

    assert result.step_count == 0
    assert result.ctx["step"] == 0
    assert "error" not in result.ctx
    assert persisted == []
