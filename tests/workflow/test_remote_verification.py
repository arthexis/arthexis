from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def _remote_verify_block() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: Verify Watchtower Remote stage")
    end = text.index("      - name: Preflight Watchtower Wire capability", start)
    return text[start:end]


def test_watchtower_remote_verification_uses_retrying_acceptance_helper():
    block = _remote_verify_block()

    assert "scripts/verify_remote_deployment.py local" in block
    assert "scripts/verify_remote_deployment.py public" in block
    assert "socket.create_connection" not in block
    assert "for port in 8000 8001" not in block


def test_watchtower_remote_verification_reports_service_diagnostics_on_failure():
    block = _remote_verify_block()

    assert "remote_diagnostics()" in block
    assert "systemctl status gway-mcp-server.service gway-remote-auth.service" in block
    assert "journalctl -u gway-mcp-server.service -u gway-remote-auth.service" in block
