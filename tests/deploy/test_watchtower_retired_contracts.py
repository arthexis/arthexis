from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

WATCHTOWER = Path(".github/workflows/watchtower-deploy.yml")


def test_watchtower_does_not_reintroduce_retired_rollover_automation() -> None:
    workflow = WATCHTOWER.read_text(encoding="utf-8")

    retired = (
        "Open next-version PR after accepted deployment",
        "Reconcile Gway next-version PR after accepted deployment",
        "release/advance-arthexis-v",
        "release/advance-gway-v",
        "rollover_pr=",
        "gway_rollover_pr=",
        "is_gway_version_only_rollover.py",
    )
    offenders = [marker for marker in retired if marker in workflow]

    assert offenders == []


def test_watchtower_does_not_depend_on_retired_gh_cli_features() -> None:
    workflow = WATCHTOWER.read_text(encoding="utf-8")

    retired = (
        "headRefOid",
        "--match-head-commit",
    )
    offenders = [marker for marker in retired if marker in workflow]

    assert offenders == []


def test_watchtower_acceptance_is_terminal_for_repository_mutation(
    watchtower_workflow,
) -> None:
    deploy = watchtower_workflow.job("deploy")
    accepted = deploy.index("- name: Record accepted Watchtower deployment")
    tail = deploy[accepted:]

    assert "gh pr create" not in tail
    assert "git push" not in tail
    assert "/pulls" not in tail
    assert "version-only" not in tail
