from pathlib import Path

SCRIPT = Path("scripts/verify_remote_deployment.py")
WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def test_remote_acceptance_checks_both_loopback_listeners() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '_wait_listener("127.0.0.1", 8000)' in script
    assert '_wait_listener("127.0.0.1", 8001)' in script


def test_remote_acceptance_validates_oauth_metadata_contract() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'ORIGIN = "https://remote.arthexis.com"' in script
    assert 'PROTECTED = f"{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in script
    assert 'AUTH_SERVER = f"{ORIGIN}/.well-known/oauth-authorization-server"' in script
    assert '"authorization_endpoint"' in script
    assert '"token_endpoint"' in script
    assert '"revocation_endpoint"' in script
    assert '"code_challenge_methods_supported"' in script
    assert '["S256"]' in script


def test_remote_acceptance_checks_protocol_appropriate_oauth_failures() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'Request(f"{ORIGIN}/oauth/authorize", method="GET")' in script
    assert "400," in script
    assert 'Request(f"{ORIGIN}/oauth/token", method="GET")' in script
    assert "405," in script


def test_remote_acceptance_requires_mcp_oauth_challenge() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "Request(RESOURCE, method=\"GET\")" in script
    assert "401" in script
    assert 'mcp.headers.get("WWW-Authenticate", "")' in script
    assert 'resource_metadata="{PROTECTED}"' in script


def test_watchtower_runs_local_acceptance_before_public_exposure() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    local = workflow.index("verify_remote_deployment.py local")
    expose = workflow.index("- name: Expose Arthexis publicly through Gway recipe")
    assert local < expose


def test_watchtower_runs_public_acceptance_after_exposure() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expose = workflow.index("- name: Expose Arthexis publicly through Gway recipe")
    public = workflow.index("verify_remote_deployment.py public")
    assert expose < public
    assert "if: env.PUBLIC_EXPOSURE_ENABLED == 'true'" in workflow
    assert 'echo "remote_public=ok"' in workflow
