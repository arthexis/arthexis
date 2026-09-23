from pathlib import Path

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")
POLICY = Path("deploy/mcp-scopes.toml")
REMOTE = Path("deploy/remote.rx")
MCP_SERVER = Path("deploy/mcp-server.rx")


def _blocks(path: Path) -> list[str]:
    blocks = []
    current = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(" ".join(current))
    return blocks


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
    commands = _blocks(REMOTE)

    assert "security scope apply deploy/mcp-scopes.toml" in commands
    assert "security scope show chatgpt-logs" in commands
    assert not any("security token create" in command for command in commands)
    assert not any("oauth token" in command.lower() for command in commands)


def test_remote_recipe_installs_mcp_service_from_stable_project_recipe() -> None:
    commands = _blocks(REMOTE)
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


def test_watchtower_mcp_service_wrapper_delegates_to_gway_sampler() -> None:
    recipe = MCP_SERVER.read_text(encoding="utf-8")

    assert "recipe mcp/server" in recipe
    assert "--host 127.0.0.1" in recipe
    assert "--port 8000" in recipe
    assert "--route /mcp" in recipe
    assert "--endpoint https://remote.arthexis.com/mcp" in recipe
    assert "--mcp-host" not in recipe
    assert "--mcp-port" not in recipe
    assert "--mcp-public-origin" not in recipe
    assert "--path /mcp" not in recipe
    assert "server serve" not in recipe
    assert "fastmcp" not in recipe.lower()
    assert "0.0.0.0" not in recipe


def test_remote_recipe_installs_builtin_remote_auth_service_on_loopback() -> None:
    commands = _blocks(REMOTE)
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


def test_watchtower_has_safe_o8a_preflight_recipe() -> None:
    recipe = Path("deploy/remote-preflight.rx").read_text(encoding="utf-8")

    assert "set env GWAY_CACHE_DIR /var/lib/gway/cache" in recipe
    assert "security scope show chatgpt-logs" in recipe
    assert "security token list" in recipe
    assert "--name mcp-server" in recipe
    assert "remote serve" in recipe
    assert "security token create" not in recipe



def test_remote_service_targets_use_bare_double_dash_continuations() -> None:
    lines = [
        line.strip()
        for line in REMOTE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    for target in (
        "./deploy/mcp-server.rx",
        "./deploy/mcp-server.rx",
        "remote serve 127.0.0.1 8001 --public-origin "
        "https://remote.arthexis.com --resource-path /mcp",
        "remote serve 127.0.0.1 8001 --public-origin "
        "https://remote.arthexis.com --resource-path /mcp",
    ):
        index = lines.index(target)
        assert lines[index - 1] == "--"

    assert "-- ./deploy/mcp-server.rx" not in lines


def test_remote_preflight_service_targets_use_bare_double_dash_continuations() -> None:
    path = Path("deploy/remote-preflight.rx")
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    mcp = lines.index("./deploy/mcp-server.rx")
    auth = lines.index(
        "remote serve 127.0.0.1 8001 --public-origin "
        "https://remote.arthexis.com --resource-path /mcp"
    )

    assert lines[mcp - 1] == "--"
    assert lines[auth - 1] == "--"
