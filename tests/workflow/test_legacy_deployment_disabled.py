"""Guard against accidental restoration of legacy deployment entry points."""

from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
RETIRED = (
    "watchtower-candidate.yml",
    "watchtower-deploy.yml",
    "watchtower-recovery.yml",
    "watchtower-dependencies.yml",
    "publish.yml",
)


@pytest.mark.parametrize("name", RETIRED)
def test_legacy_deployment_workflow_is_inert(name: str) -> None:
    source = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "    if: github.event_name == 'push'" in source
    assert "  repository_dispatch:" not in source
    assert "  pull_request_target:" not in source
    assert "  push:" not in source
    assert "Legacy automation disabled" in source
