from pathlib import Path

from gway.recipe import load_recipe

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")
POLICY = Path("deploy/mcp-scopes.toml")
REMOTE = Path("deploy/remote.rx")
MCP_SERVER = Path("deploy/mcp-server.rx")


def _commands(path: Path) -> list[str]:
    commands, _ = load_recipe(path)
    return [" ".join(str(token) for token in command["tokens"]) for command in commands]


def _remote_step() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("- name: Provision Watchtower remote policy and services")
    end = workflow.index("- name: Expose Arthexis publicly through Gway recipe", start)
    return workflow[start:end]


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


def test_remote_recipe_applies_checked_in_policy_without_creating_tokens() -> None:
    commands = _commands(REMOTE)

    assert "security scope apply deploy/mcp-scopes.toml" in commands
    assert "security scope show chatgpt-logs" in commands
    assert not any("security token create" in command for command in commands)
    assert not any("oauth token" in command.lower() for command in commands)


def test_remote_recipe_installs_mcp_service_from_stable_project_recipe() -> None:
    commands = _commands(REMOTE)
    install = next(
        command
        for command in commands
        if command.startswith("service install") and "--name mcp-server" in command
    )
    restart = next(
        command
        for command in commands
        if command.startswith("service restart") and "--name mcp-server" in command
    )

    assert "--backend systemd" in install
    assert "--system" in install
    assert "--environment GWAY_CACHE_DIR=[GWAY_CACHE_DIR|/var/lib/gway/cache]" in install
    assert "-- ./deploy/mcp-server.rx" in install
    assert "--timeout 40" in restart
    assert "-- ./deploy/mcp-server.rx" in restart


def test_watchtower_mcp_service_wrapper_is_loopback_and_public_origin_aware() -> None:
    commands = _commands(MCP_SERVER)

    assert commands[0] == "require fastmcp>=4,<5"
    serve = commands[1]
    assert serve.startswith("server serve 127.0.0.1 8000 /mcp")
    assert "--public-origin https://remote.arthexis.com" in serve
    assert "0.0.0.0" not in serve


def test_remote_recipe_installs_builtin_remote_auth_service_on_loopback() -> None:
    commands = _commands(REMOTE)
    install = next(
        command
        for command in commands
        if command.startswith("service install") and "remote serve" in command
    )
    restart = next(
        command
        for command in commands
        if command.startswith("service restart") and "remote serve" in command
    )

    for command in (install, restart):
        assert "remote serve 127.0.0.1 8001" in command
        assert "--public-origin https://remote.arthexis.com" in command
        assert "--resource-path /mcp" in command
        assert "0.0.0.0" not in command

    assert "--backend systemd" in install
    assert "--system" in install
    assert "--environment GWAY_CACHE_DIR=[GWAY_CACHE_DIR|/var/lib/gway/cache]" in install
    assert "--timeout 40" in restart


def test_watchtower_workflow_delegates_remote_provisioning_to_recipe() -> None:
    step = _remote_step()

    assert ".venv/bin/python -m gway ./deploy/remote.rx" in step
    assert 'install -d -m 0700 -o root -g root "${WATCHTOWER_GWAY_CACHE_DIR}"' in step
    assert '${WATCHTOWER_GWAY_CACHE_DIR}/security/state.sqlite' in step
    assert "systemctl is-active --quiet gway-mcp-server.service" in step
    assert "systemctl is-active --quiet gway-remote-auth.service" in step


def test_watchtower_workflow_no_longer_reimplements_mcp_service_setup() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "from gway.sampler import root" not in workflow
    assert '"mcp" / "server.rx"' not in workflow
    assert "service install --backend systemd --system --name mcp-server" not in workflow
    assert "security token create" not in workflow


def test_watchtower_remote_uses_durable_gway_cache_root() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "WATCHTOWER_GWAY_CACHE_DIR: /var/lib/gway/cache" in workflow
    assert 'GWAY_CACHE_DIR="\'"${WATCHTOWER_GWAY_CACHE_DIR}"\'"' in workflow
