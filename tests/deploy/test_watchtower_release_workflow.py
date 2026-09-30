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


def test_release_runlevel_is_the_publication_trigger(watchtower_deploy_workflow: str):
    workflow = watchtower_deploy_workflow
    deploy = workflow.split("- name: Reconcile certified package publisher handoff", 1)[1]

    assert "if: fromJSON(env.WATCHTOWER_LEVEL) >= 3" in deploy
    assert "_publish=not_requested" not in deploy
    assert "/commits/{release['sha']}/pulls" not in deploy
    assert 'label.get("name") == "release"' not in deploy


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

    assert "forced_by_release_or_deploy_label" not in gate
    assert "needs.queue-gate.outputs.deploy == 'true'" in deploy


def test_watchtower_stages_are_cumulative_runlevels(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    dispatch = workflow.split("workflow_dispatch:", 1)[1].split("concurrency:", 1)[0]

    assert "default: 2-remote" in dispatch
    assert "- 0-gway" in dispatch
    assert "- 1-arthexis" in dispatch
    assert "- 2-remote" in dispatch
    assert "- 3-release" in dispatch
    assert "remote-only" not in workflow
    assert "stage_level: ${{ steps.change.outputs.stage_level }}" in workflow
    assert "0-gway) stage_level=0" in workflow
    assert "1-arthexis) stage_level=1" in workflow
    assert "2-remote) stage_level=2" in workflow
    assert "3-release) stage_level=3" in workflow


def test_watchtower_stage_gates_are_cumulative(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow

    assert "- name: Install exact canonical Gway\n        if: fromJSON(env.WATCHTOWER_LEVEL) >= 0" in workflow
    assert "- name: Deploy Arthexis through Gway system scope\n        if: fromJSON(env.WATCHTOWER_LEVEL) >= 1" in workflow
    assert "- name: Converge Watchtower Remote stage\n        if: fromJSON(env.WATCHTOWER_LEVEL) >= 2" in workflow
    assert "- name: Verify Watchtower Wire capability\n        if: fromJSON(env.WATCHTOWER_LEVEL) >= 2" in workflow
    assert "- name: Record accepted Watchtower deployment\n        if: fromJSON(env.WATCHTOWER_LEVEL) >= 2" in workflow
    assert "- name: Reconcile certified package publisher handoff\n        if: fromJSON(env.WATCHTOWER_LEVEL) >= 3" in workflow


def test_release_stage_uses_current_candidate_pair(watchtower_workflow) -> None:
    pair = watchtower_workflow.step("Resolve exact deployment pair")

    assert "accepted-release" not in pair
    assert "Manual release requires a valid accepted" not in pair
    assert 'gway_sha="$current_gway"' in pair


def test_watchtower_normal_queue_counts_runnable_non_hold_prs(watchtower_workflow) -> None:
    gate = watchtower_workflow.job("queue-gate")
    assert "for repository in arthexis/arthexis arthexis/gway; do" in gate
    assert "gh pr list --repo" in gate
    assert "|on-hold|" in gate
    assert "|on hold|" in gate
    assert "|needs-watchtower|" in gate
    assert "waiting-merge" in gate
    assert "waiting-watchtower" in gate
    assert "watchtower_dependency.py" in gate
    assert "draft" not in gate.lower()
    assert 'echo "deploy=false" >> "$GITHUB_OUTPUT"' in gate
    assert 'echo "deploy=true" >> "$GITHUB_OUTPUT"' in gate
    assert "watchtower_deploy=coalesced_active_pr_queue" in gate
    assert "watchtower_deploy=dependency_unblock_or_queue_drained" in gate


def test_watchtower_acceptance_does_not_mutate_product_repositories(
    watchtower_workflow,
) -> None:
    deploy = watchtower_workflow.job("deploy")
    accepted = deploy.index("- name: Record accepted Watchtower deployment")
    tail = deploy[accepted:]

    assert "gh pr create" not in tail
    assert "git checkout -B" not in tail
    assert "git push" not in tail
