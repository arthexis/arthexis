from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "live-integration.yml"
SCRIPT = ROOT / "scripts" / "ci" / "live-integration.sh"


def test_live_integration_is_manual_serialized_and_on_physical_runner() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "workflow_run:" not in text
    assert "runs-on: [self-hosted, Linux, X64, arthexis-ci]" in text
    assert "group: arthexis-live-integration" in text
    assert "cancel-in-progress: false" in text


def test_live_integration_targets_exact_current_main_revision() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert "git ls-remote https://github.com/arthexis/arthexis.git refs/heads/main" in workflow
    assert "ARTHEXIS_EXPECTED_SHA" in workflow
    assert '[[ "$current_main_sha" != "$expected_sha" ]]' in script
    assert "git -C \"$checkout\" rev-parse HEAD" in script
    assert '[[ "$deployed_sha" != "$expected_sha" ]]' in script


def test_live_integration_exercises_gway_managed_lifecycle_and_health() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "sudo -n gway upgrade arthexis" in script
    assert "sudo -n gway install arthexis" in script
    assert '[[ "$checkout" != "/opt/arthexis/app" ]]' in script
    assert "sudo -n --preserve-env=GWAY_SERVICE_PROFILE gway service install arthexis" in script
    assert "sudo -n --preserve-env=GWAY_SERVICE_PROFILE gway service start arthexis" in script
    assert "gway service status arthexis" in script
    assert "gway arthexis good" in script
