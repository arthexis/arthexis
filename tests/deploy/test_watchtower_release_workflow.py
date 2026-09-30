import pytest
import subprocess
from pathlib import Path


pytestmark = pytest.mark.workflow


def test_watchtower_gway_version_awk_extracts_project_version():
    pyproject = """[build-system]
requires = ["setuptools"]

[project]
name = "gway"
version = "0.4.59"
requires-python = ">=3.10"

[project.optional-dependencies]
toml = ["tomli>=2; python_version < '3.11'"]
"""
    program = r"""
/^\[project\][[:space:]]*$/ { in_project=1; next }
/^\[/ && in_project { exit }
in_project && /^[[:space:]]*version[[:space:]]*=/ {
  value=$0
  sub(/^[^=]*=[[:space:]]*/, "", value)
  sub(/[[:space:]]*#.*/, "", value)
  gsub(/^[[:space:]]*["']|["'][[:space:]]*$/, "", value)
  print value
  exit
}
"""
    result = subprocess.run(
        ["awk", program],
        input=pyproject,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "0.4.59"


def test_watchtower_release_handoff_is_recoverable_without_gh_cli(watchtower_deploy_workflow: str):
    workflow = watchtower_deploy_workflow

    assert "gh workflow run" not in workflow
    assert ".watchtower/releases/{package}/{version}.json" in workflow
    assert "/actions/workflows/" in workflow
    assert "/dispatches" in workflow
    assert '_publish=already_recorded' in workflow
    assert '_publish=dispatched' in workflow


def test_watchtower_requires_release_label_before_dispatch(watchtower_deploy_workflow: str):
    workflow = watchtower_deploy_workflow

    assert "_publish=not_requested" in workflow
    assert "/commits/{release['sha']}/pulls" in workflow
    assert 'label.get("name") == "release"' in workflow


def test_arthexis_deploy_uses_current_gway_main(watchtower_workflow) -> None:
    pair = watchtower_workflow.step("Resolve exact deployment pair")

    assert 'gway_sha="$current_gway"' in pair
    assert 'gway_sha="${previous_gway:-$current_gway}"' not in pair




def test_watchtower_timing_controls_shared_queue(watchtower_workflow) -> None:
    classify = watchtower_workflow.job("classify")
    gate = watchtower_workflow.job("queue-gate")

    assert "timing: ${{ steps.change.outputs.timing }}" in classify
    assert "force_deploy" not in classify
    assert "github.event.client_payload.timing || 'queued'" in classify
    assert 'timing="${{ inputs.timing }}"' in classify
    assert '[[ "$timing" != "queued" && "$timing" != "immediate" ]]' in classify
    assert "TIMING: ${{ needs.classify.outputs.timing }}" in gate
    assert 'if [[ "$TIMING" == "immediate" ]]; then' in gate
    assert "watchtower_deploy=immediate" in gate
    assert "forced_by_release_or_deploy_label" not in gate


def test_watchtower_manual_dispatch_exposes_timing_choice(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    dispatch = workflow.split("workflow_dispatch:", 1)[1].split("concurrency:", 1)[0]

    assert "timing:" in dispatch
    assert "default: queued" in dispatch
    assert "- queued" in dispatch
    assert "- immediate" in dispatch


def test_watchtower_release_obeys_queue_timing(watchtower_workflow) -> None:
    gate = watchtower_workflow.job("queue-gate")
    deploy = watchtower_workflow.job("deploy")

    assert 'needs.classify.outputs.stage == "release"' not in gate
    assert "forced_by_release_or_deploy_label" not in gate
    assert "needs.classify.outputs.stage == 'remote-only' || needs.queue-gate.outputs.deploy == 'true'" in deploy


def test_watchtower_normal_queue_still_counts_all_non_hold_prs(watchtower_workflow) -> None:
    gate = watchtower_workflow.job("queue-gate")
    assert "for repository in arthexis/arthexis arthexis/gway; do" in gate
    assert "pulls?state=open&per_page=100" in gate
    assert '. == "on-hold"' in gate
    assert '. == "on hold"' in gate
    assert "draft" not in gate.lower()
    assert 'echo "deploy=false" >> "$GITHUB_OUTPUT"' in gate
    assert 'echo "deploy=true" >> "$GITHUB_OUTPUT"' in gate
    assert "watchtower_deploy=coalesced_active_pr_queue" in gate


def test_manual_release_pins_both_packages_to_accepted_manifest(watchtower_workflow):
    pair = watchtower_workflow.step("Resolve exact deployment pair")
    release_branch = pair.split(
        'if [[ "$RELEASE_INTENT" == "manual" ]]; then', 1
    )[1].split('elif [[ "$EVENT_NAME" == "repository_dispatch" ]]; then', 1)[0]

    assert 'arthexis_sha="$previous_arthexis"' in release_branch
    assert 'gway_sha="$previous_gway"' in release_branch
    assert 'source="accepted-release"' in release_branch
    assert 'requested_arthexis=' not in release_branch
    assert 'current_arthexis' not in release_branch
    assert 'current_gway' not in release_branch


def test_manual_release_requires_valid_accepted_pair(watchtower_workflow):
    pair = watchtower_workflow.step("Resolve exact deployment pair")
    release_branch = pair.split(
        'if [[ "$RELEASE_INTENT" == "manual" ]]; then', 1
    )[1].split('elif [[ "$EVENT_NAME" == "repository_dispatch" ]]; then', 1)[0]

    assert "Manual release requires a valid accepted Arthexis SHA." in release_branch
    assert "Manual release requires a valid accepted Gway SHA." in release_branch


def test_watchtower_acceptance_does_not_mutate_product_repositories(
    watchtower_workflow,
) -> None:
    deploy = watchtower_workflow.job("deploy")
    accepted = deploy.index("- name: Record accepted Watchtower deployment")
    tail = deploy[accepted:]

    assert "gh pr create" not in tail
    assert "git checkout -B" not in tail
    assert "git push" not in tail
