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


def test_arthexis_deploy_uses_current_gway_main(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow

    assert 'gway_sha="$current_gway"' in workflow
    assert 'gway_sha="${previous_gway:-$current_gway}"' not in workflow




def test_watchtower_deploy_label_bypasses_shared_queue(
    watchtower_deploy_workflow: str,
) -> None:
    workflow = watchtower_deploy_workflow

    assert "force_deploy: ${{ steps.change.outputs.force_deploy }}" in workflow
    assert 'grep -Fxq deploy <<<"$labels"' in workflow
    assert "github.event.client_payload.force_deploy || 'false'" in workflow
    assert "FORCE_DEPLOY: ${{ needs.classify.outputs.force_deploy }}" in workflow
    assert 'if [[ "$FORCE_DEPLOY" == "true" || "${{ needs.classify.outputs.stage }}" == "release" ]]; then' in workflow
    assert "watchtower_deploy=forced_by_release_or_deploy_label" in workflow


def test_watchtower_normal_queue_still_counts_all_non_hold_prs(
    watchtower_deploy_workflow: str,
) -> None:
    workflow = watchtower_deploy_workflow

    gate = workflow.split("queue-gate:", 1)[1].split("\n\n  deploy:", 1)[0]
    assert "arthexis/arthexis arthexis/gway" in gate
    assert '. == "on-hold"' in gate
    assert '. == "on hold"' in gate
    assert "draft" not in gate.lower()
    assert "watchtower_deploy=coalesced_active_pr_queue" in gate


def _commit_pyproject(repo: Path, version: str, *, extra: str = "") -> str:
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "gway"\n'
        f'version = "{version}"\n'
        + extra,
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"version {version}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_gway_version_only_detector_accepts_patch_only_rollover(tmp_path: Path) -> None:
    repo = tmp_path / "gway"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "CI"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.invalid"], check=True)

    before = _commit_pyproject(repo, "0.4.60")
    after = _commit_pyproject(repo, "0.4.61")

    result = subprocess.run(
        [
            "python",
            ".github/scripts/is_gway_version_only_rollover.py",
            str(repo),
            before,
            after,
        ],
        check=False,
    )
    assert result.returncode == 0


def test_gway_version_only_detector_rejects_other_pyproject_changes(tmp_path: Path) -> None:
    repo = tmp_path / "gway"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "CI"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.invalid"], check=True)

    before = _commit_pyproject(repo, "0.4.60")
    after = _commit_pyproject(repo, "0.4.61", extra='description = "changed"\n')

    result = subprocess.run(
        [
            "python",
            ".github/scripts/is_gway_version_only_rollover.py",
            str(repo),
            before,
            after,
        ],
        check=False,
    )
    assert result.returncode == 1


def test_watchtower_reconciles_stranded_gway_rollover(
    watchtower_deploy_workflow: str,
) -> None:
    workflow = watchtower_deploy_workflow

    step = workflow.split(
        "- name: Reconcile Gway next-version PR after accepted deployment", 1
    )[1].split("- name: Restore previous Gway runtime after failed deployment", 1)[0]

    assert "if: env.WATCHTOWER_STAGE == 'arthexis'" in step
    assert "GWAY_CHANGED" not in step
    assert "is_gway_version_only_rollover.py" in step
    assert "gway_rollover=suppressed_version_only" in step
    assert 'git clone --quiet --depth=2 https://github.com/arthexis/gway.git "$work"' in step
    assert 'remote_branch_sha="$(git ls-remote --heads origin "refs/heads/$branch"' in step
    assert '--force-with-lease="refs/heads/$branch:$remote_branch_sha"' in step
    assert '--force-with-lease="refs/heads/$branch:"' in step
