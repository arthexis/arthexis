from pathlib import Path

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def _mcp_step() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("- name: Install and start Gway MCP systemd service")
    end = workflow.index("- name: Expose Arthexis publicly through Gway recipe", start)
    return workflow[start:end]


def test_watchtower_deploy_installs_mcp_from_persistent_gway_runtime() -> None:
    step = _mcp_step()

    assert 'runtime_python="/var/lib/gway/projects/arthexis/.venv/bin/python"' in step
    assert 'from gway.sampler import root' in step
    assert '"mcp" / "server.rx"' in step
    assert "RUNNER_TEMP" not in step


def test_watchtower_deploy_uses_generic_mcp_service_lifecycle() -> None:
    step = _mcp_step()

    assert "service install --backend systemd --system --name mcp-server" in step
    assert "service restart --system --name mcp-server --timeout 40" in step
    assert "systemctl is-active --quiet gway-mcp-server.service" in step


def test_watchtower_deploy_waits_for_local_mcp_listener() -> None:
    step = _mcp_step()

    assert 'socket.create_connection(("127.0.0.1", 8000)' in step
    assert "MCP service did not open 127.0.0.1:8000" in step
