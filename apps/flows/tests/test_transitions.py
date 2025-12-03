"""Ensure workflow transitions are persisted for later reconstruction."""

import pytest

from apps.flows import NodeWorkflow, NodeWorkflowStep
from apps.flows.models import Transition


@pytest.mark.django_db
def test_transitions_capture_step_history():
    def _first_step(context):
        context["first_ran"] = True
        return {"state": "one"}

    def _second_step(context):
        context["second_ran"] = True
        return {"state": "two"}

    workflow = NodeWorkflow(
        "sample_flow",
        [
            NodeWorkflowStep.from_callable(_first_step, name="first"),
            NodeWorkflowStep.from_callable(_second_step, name="second"),
        ],
    )

    def _run_step(step, context):
        return step.func(context)

    workflow.run("abc123", context={"step": 0}, run_step=_run_step)

    transitions = list(
        Transition.objects.filter(workflow="sample_flow", identifier="abc123")
        .order_by("occurred_at")
        .all()
    )

    assert [t.from_state for t in transitions] == ["0", "1"]
    assert [t.to_state for t in transitions] == ["1", "2"]
    assert [t.step_name for t in transitions] == ["first", "second"]
    assert all(t.occurred_at is not None for t in transitions)
