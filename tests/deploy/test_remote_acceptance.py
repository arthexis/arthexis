from pathlib import Path

import pytest

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


@pytest.mark.workflow
def test_base_watchtower_stage_includes_remote_acceptance() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "stage:" in workflow
    assert "default: 2-remote" in workflow
    assert "- name: Preflight Watchtower Remote stage" in workflow
    assert "- name: Converge Watchtower Remote stage" in workflow
    assert "- name: Verify Watchtower Remote stage" in workflow
    assert "if: fromJSON(env.WATCHTOWER_LEVEL) >= 2" in workflow
    assert "remote-only" not in workflow
    assert "gway --recipe deploy/remote.rx" in workflow
    assert "gway --recipe deploy/remote-expose.rx" in workflow
    assert "verify_remote_deployment.py local" in workflow
    assert "verify_remote_deployment.py public" in workflow


def test_remote_acceptance_validates_projected_mcp_tool_sets() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'expected_tools={"query", "tail"}' in script
    assert 'query_command="log sources"' in script
    assert 'expected_tools={"gway", "query", "tail"}' in script
    assert 'if "gway" in tools:' in script
    assert 'MCP gway tool is not advertised mutating' in script


@pytest.mark.workflow
def test_remote_acceptance_waits_for_listeners_before_systemd_active_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("      - name: Verify Watchtower Remote stage")
    end = workflow.index("      - name: Preflight Watchtower Wire capability", start)
    block = workflow[start:end]

    listener = block.index("verify_remote_deployment.py local")
    mcp_active = block.index("systemctl is-active --quiet gway-mcp-server.service")
    auth_active = block.index("systemctl is-active --quiet gway-remote-auth.service")

    assert listener < mcp_active
    assert listener < auth_active
    assert "gway_mcp_server_active=failed" in block
    assert "gway_remote_auth_active=failed" in block
    assert "remote_diagnostics" in block
