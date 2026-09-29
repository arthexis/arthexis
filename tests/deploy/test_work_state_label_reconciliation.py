from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow


def test_native_auto_merge_guard_uses_github_state_not_work_labels() -> None:
    workflow = Path(
        ".github/workflows/native-auto-merge-guard.yml"
    ).read_text(encoding="utf-8")

    assert "Native Auto-Merge Guard" in workflow
    assert "converted_to_draft" in workflow
    assert '"on-hold"' in workflow
    assert '"on hold"' in workflow
    assert "--disable-auto" in workflow
    assert '== "approved"' not in workflow
    assert '== "in-progress"' not in workflow
    assert "remove-label approved" not in workflow
    assert "remove-label in-progress" not in workflow


def test_branch_update_releases_hold_without_using_work_state_lock() -> None:
    workflow = Path(
        ".github/workflows/approved-branch-update.yml"
    ).read_text(encoding="utf-8")

    assert "expected_head_sha=$head_sha" in workflow
    assert "types: [opened, reopened, synchronize, ready_for_review, unlabeled]" in workflow
    assert 'on-hold|"on hold"' in workflow
    assert "labels[]=in-progress" not in workflow
    assert "remove-label in-progress" not in workflow
