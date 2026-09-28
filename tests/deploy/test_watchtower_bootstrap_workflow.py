from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "watchtower-deploy.yml"


def test_watchtower_deploy_converges_certified_gway_bootstrap():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Converge public Gway bootstrap" in workflow
    assert "/var/lib/gway/venv/share/gway/sampler/bootstrap/watchtower.rx" in workflow
    assert "https://install.arthexis.com/gway" in workflow
    assert "# GWAY_BOOTSTRAP_V1" in workflow
    assert "git+https://github.com/arthexis/gway" in workflow
    assert "Public Gway bootstrap must install a certified release" in workflow
