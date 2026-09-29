from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

CORE_WORKFLOWS = (
    "python-package.yml",
    "python-quality.yml",
    "python-compatibility.yml",
    "secret-scan.yml",
)


@pytest.mark.parametrize("workflow", CORE_WORKFLOWS)
def test_label_changes_do_not_trigger_core_ci(workflow: str) -> None:
    text = (Path(".github/workflows") / workflow).read_text(encoding="utf-8")
    trigger = text.split("workflow_dispatch:", 1)[0]

    assert "labeled" not in trigger
    assert "unlabeled" not in trigger
