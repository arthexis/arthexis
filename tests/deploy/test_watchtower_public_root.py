from pathlib import Path


WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def test_base_watchtower_stage_excludes_public_surface_convergence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "default: arthexis" in workflow
    assert "Expose Arthexis publicly through Gway recipe" not in workflow
    assert "Verify public Markdown root" not in workflow
    assert "./deploy/remote-dns.rx" not in workflow
    assert "remote.arthexis.com" not in workflow


def test_wire_stage_keeps_public_health_verification_bounded() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "- name: Verify Watchtower Wire stage" in workflow
    assert "https://register.arthexis.com/health" in workflow
    assert "curl --fail --silent --show-error --retry 5 --retry-delay 1 --retry-all-errors" in workflow
    assert "set -x" not in workflow


def test_wire_stage_uses_certbot_secret_without_dns_credentials() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "ARTHEXIS_CERTBOT_EMAIL: ${{ secrets.ARTHEXIS_CERTBOT_EMAIL }}" in workflow
    assert "ACTIONS_GODADDY_PAT" not in workflow
    assert "ACTIONS_GODADDY_API_KEY" not in workflow
    assert "ACTIONS_GODADDY_API_SECRET" not in workflow


def test_wire_preflight_records_service_and_listener_state() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "systemctl show gway-wire-enroll.service gway-wireguard-enroll.service" in workflow
    assert "sport = :8787" in workflow
    assert "gway -e wire watchtower" not in workflow
    assert "test -f /var/lib/gway/venv/share/gway/sampler/wire/watchtower.rx" in workflow
    assert "gway --json resolve wire watchtower" in workflow
    assert "sampler/wire/watchtower.rx" in workflow
