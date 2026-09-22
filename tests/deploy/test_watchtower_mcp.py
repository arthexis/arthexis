from pathlib import Path

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")
POLICY = Path("deploy/mcp-scopes.toml")


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
    assert "time.monotonic() + 60" in step
    assert "MCP service did not open 127.0.0.1:8000" in step
    assert "systemctl status gway-mcp-server.service --no-pager" in step
    assert "journalctl -u gway-mcp-server.service -n 100 --no-pager" in step



def test_watchtower_mcp_policy_is_read_only_logs_scope() -> None:
    policy = POLICY.read_text(encoding="utf-8")

    assert "[scopes.chatgpt-logs]" in policy
    assert '"log.sources"' in policy
    assert '"log.read"' in policy
    assert '"log.tail"' in policy
    assert '"log.search"' in policy
    assert "environment = []" in policy
    for forbidden in ("clear", "service.", "security.", "__all__"):
        assert forbidden not in policy


def test_watchtower_deploy_applies_checked_in_mcp_policy() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("- name: Apply Watchtower MCP logging scope")
    end = workflow.index("- name: Install and start Gway MCP systemd service", start)
    step = workflow[start:end]

    assert 'scope_file="/var/lib/gway/projects/arthexis/deploy/mcp-scopes.toml"' in step
    assert "security scope apply deploy/mcp-scopes.toml" in step
    assert "security scope show chatgpt-logs" in step
    assert "security token create" not in step


def test_watchtower_mcp_uses_durable_gway_cache_root() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "WATCHTOWER_GWAY_CACHE_DIR: /var/lib/gway/cache" in workflow
    assert 'install -d -m 0700 -o root -g root "${WATCHTOWER_GWAY_CACHE_DIR}"' in workflow
    assert 'GWAY_CACHE_DIR="\'"${WATCHTOWER_GWAY_CACHE_DIR}"\'"' in workflow
    assert '--environment GWAY_CACHE_DIR="\'"${WATCHTOWER_GWAY_CACHE_DIR}"\'"' in workflow
    assert '${WATCHTOWER_GWAY_CACHE_DIR}/security/state.sqlite' in workflow
