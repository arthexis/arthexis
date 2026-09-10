from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "live-smoke.yml"
SCRIPT = ROOT / "scripts" / "ci" / "live-smoke.sh"


def test_live_smoke_is_gated_by_successful_install_health() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_run:" in text
    assert "- Install Health Check" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "runs-on: [self-hosted, Linux, X64, arthexis-ci]" in text
    assert "cancel-in-progress: false" in text


def test_live_smoke_checks_exact_validated_revision_before_deploying() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert "github.event.workflow_run.head_sha" in workflow
    assert "ARTHEXIS_EXPECTED_SHA" in workflow
    assert (
        "git ls-remote https://github.com/arthexis/arthexis.git refs/heads/main"
        in script
    )
    assert '[[ "$current_sha" != "$expected_sha" ]]' in script
    assert "git -C /opt/arthexis/app rev-parse HEAD" in script
    assert '[[ "$deployed_sha" != "$expected_sha" ]]' in script


def test_live_smoke_exercises_gway_managed_install_and_services() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "sudo -n gway upgrade arthexis" in script
    assert "sudo -n gway install arthexis" in script
    assert (
        "sudo -n --preserve-env=GWAY_SERVICE_PROFILE gway service install arthexis"
        in script
    )
    assert (
        "sudo -n --preserve-env=GWAY_SERVICE_PROFILE gway service start arthexis"
        in script
    )
    assert "gway service status arthexis" in script
    assert "gway arthexis good" in script
