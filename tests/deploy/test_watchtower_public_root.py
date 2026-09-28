def test_base_watchtower_stage_excludes_public_surface_convergence(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    assert "default: arthexis" in workflow
    assert "Expose Arthexis publicly through Gway recipe" not in workflow
    assert "Verify public Markdown root" not in workflow
    assert "./deploy/remote-dns.rx" not in workflow
    assert "remote.arthexis.com" not in workflow


def test_remote_stage_keeps_wire_public_health_verification_bounded(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    assert "- name: Verify Watchtower Wire capability" in workflow
    assert "https://register.arthexis.com/health" in workflow
    assert "curl --fail --silent --show-error --retry 5 --retry-delay 1 --retry-all-errors" in workflow
    assert "set -x" not in workflow


def test_remote_wire_capability_uses_certbot_secret_without_dns_credentials(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    assert "ARTHEXIS_CERTBOT_EMAIL: ${{ secrets.ARTHEXIS_CERTBOT_EMAIL }}" in workflow
    assert "ACTIONS_GODADDY_PAT" not in workflow
    assert "ACTIONS_GODADDY_API_KEY" not in workflow
    assert "ACTIONS_GODADDY_API_SECRET" not in workflow


def test_remote_wire_preflight_records_service_and_listener_state(watchtower_deploy_workflow: str) -> None:
    workflow = watchtower_deploy_workflow
    assert "systemctl show gway-wire-enroll.service gway-wireguard-enroll.service" in workflow
    assert "sport = :8787" in workflow
    assert "gway -e wire watchtower" not in workflow
    assert "test -f /var/lib/gway/venv/share/gway/sampler/wire/watchtower.rx" in workflow
    assert "gway --json resolve wire watchtower" in workflow
    assert "sampler/wire/watchtower.rx" in workflow
