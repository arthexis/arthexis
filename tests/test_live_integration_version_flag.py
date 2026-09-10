from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "live-integration.yml"


def test_live_integration_uses_global_gway_version_flag() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "gway --version" in text
    assert "gway version" not in text
