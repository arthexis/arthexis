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


def test_watchtower_mcp_policy_has_read_only_remote_scopes() -> None:
    policy = POLICY.read_text(encoding="utf-8")

    assert "[scopes.chatgpt-logs]" in policy
    assert "[scopes.chatgpt-actions]" in policy
    assert '"help"' in policy
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
    assert "security scope show chatgpt-actions" in commands
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
    assert "--environment GWAY_CACHE_DIR" not in install
    assert "-- ./mcp-server.rx" in install
    assert "--timeout 40" in restart
    assert "-- ./mcp-server.rx" in restart


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
    assert "--environment GWAY_CACHE_DIR" not in install
    assert "--timeout 40" in restart


def test_watchtower_workflow_delegates_remote_provisioning_to_recipe() -> None:
    step = _remote_step()

    assert "/usr/local/bin/gway ./deploy/remote.rx" in step
    assert "install -d -m 0700 -o root -g root /var/lib/gway/cache" in step
    assert "/var/lib/gway/cache/security/state.sqlite" in step
    assert "GWAY_CACHE_DIR" not in step
    assert "systemctl is-active --quiet gway-mcp-server.service" in step
    assert "systemctl is-active --quiet gway-remote-auth.service" in step


def test_watchtower_workflow_no_longer_reimplements_mcp_service_setup() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "from gway.sampler import root" not in workflow
    assert '"mcp" / "server.rx"' not in workflow
    assert "service install --backend systemd --system --name mcp-server" not in workflow
    assert "security token create" not in workflow


def test_watchtower_remote_uses_semantic_gway_cache_root() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    remote = REMOTE.read_text(encoding="utf-8")
    step = _remote_step()

    assert "[tool.gway.variables]" in project
    assert 'cache_dir = "/var/lib/gway/cache"' in project
    assert "GWAY_CACHE_DIR" not in step
    assert "GWAY_CACHE_DIR" not in remote

def test_remote_dns_recipe_stays_credential_free() -> None:
    recipe = Path("deploy/remote-dns.rx").read_text(encoding="utf-8")

    assert "dns create remote.arthexis.com" in recipe
    for forbidden in (
        "GODADDY_",
        "GWAY_SECRETS_DIR",
        "/etc/gway/secrets",
        "api_key",
        "api_secret",
        "pat",
        "env ",
        "set env",
    ):
        assert forbidden not in recipe


def test_watchtower_has_safe_o8a_preflight_recipe() -> None:
    recipe = Path("deploy/remote-preflight.rx").read_text(encoding="utf-8")

    assert "GWAY_CACHE_DIR" not in recipe
    assert "set env " not in recipe
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
    targets = {
        "./mcp-server.rx": 2,
        (
            "remote serve 127.0.0.1 8001 --public-origin "
            "https://remote.arthexis.com --resource-path /mcp"
        ): 2,
    }

    for target, expected_count in targets.items():
        indexes = [index for index, line in enumerate(lines) if line == target]
        assert len(indexes) == expected_count
        assert all(lines[index - 1] == "--" for index in indexes)

    assert "-- ./mcp-server.rx" not in lines


def test_remote_preflight_service_targets_use_bare_double_dash_continuations() -> None:
    path = Path("deploy/remote-preflight.rx")
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    mcp = lines.index("./mcp-server.rx")
    auth = lines.index(
        "remote serve 127.0.0.1 8001 --public-origin "
        "https://remote.arthexis.com --resource-path /mcp"
    )

    assert lines[mcp - 1] == "--"
    assert lines[auth - 1] == "--"



def test_remote_mcp_recipe_targets_are_relative_to_current_recipe_directory() -> None:
    for path in (Path("deploy/remote.rx"), Path("deploy/remote-preflight.rx")):
        recipe = path.read_text(encoding="utf-8")

        assert "./mcp-server.rx" in recipe
        assert "./deploy/mcp-server.rx" not in recipe



def test_watchtower_public_exposure_uses_certbot_actions_variable() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "ARTHEXIS_CERTBOT_EMAIL: ${{ secrets.ARTHEXIS_CERTBOT_EMAIL }}" in workflow
    assert "vars.ARTHEXIS_CERTBOT_EMAIL" not in workflow



def test_public_remote_verifier_exercises_query_and_mcp_product_contract() -> None:
    verifier = Path("scripts/verify_remote_deployment.py").read_text(
        encoding="utf-8"
    )

    for marker in (
        'urlencode({"c": "log sources"})',
        '"Cache-Control"',
        'urlencode({"c": "clear"})',
        '"mutation_not_allowed"',
        '"method": "initialize"',
        '"method": "tools/list"',
        'set(tools) != {"gway", "query"}',
        'annotations.get("readOnlyHint") is not True',
        '"name": "query"',
        '"arguments": {"command": "log sources"}',
    ):
        assert marker in verifier


def test_public_remote_verifier_uses_ephemeral_scoped_credentials() -> None:
    verifier = Path("scripts/verify_remote_deployment.py").read_text(
        encoding="utf-8"
    )

    assert 'scopes={"chatgpt-logs"}' in verifier
    assert 'operations={"clear"}' in verifier
    assert 'finally:' in verifier
    assert 'tokens.remove(read_token_name)' in verifier
    assert 'tokens.remove(mutate_token_name)' in verifier
    assert 'scopes.remove(mutate_scope_name)' in verifier
    assert 'print(read_bearer)' not in verifier
    assert 'print(mutate_bearer)' not in verifier


def test_watchtower_installs_canonical_admin_gway_launcher() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Install canonical Watchtower Gway admin launcher" in workflow
    assert "export GWAY_CACHE_DIR=/var/lib/gway/cache" in workflow
    assert "cd /var/lib/gway/projects/arthexis" not in workflow
    assert 'exec /var/lib/gway/venv/bin/gway "$@"' in workflow
    assert 'install -m 0755 "${launcher}" /usr/local/bin/gway' in workflow
    assert "/usr/local/bin/gway help security oauth client create" in workflow
    assert "/usr/local/bin/gway security oauth client list" in workflow


def test_arthexis_product_runtime_is_separate_from_gway() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "test -f /opt/arthexis/pyproject.toml" in workflow
    assert "test -f /opt/arthexis/install.sh" in workflow
    assert "/opt/arthexis/.venv/bin/python -m pip install \"gway" not in workflow
    assert "Arthexis product venv must not contain GWAY" in workflow
    assert "/var/lib/gway/venv/bin/gway" in workflow
    assert "service=active_independent_of_gway" in workflow
    assert "ExecStart must not depend on GWAY" in workflow


def test_chatgpt_logs_scope_includes_help_for_existing_tokens() -> None:
    policy = POLICY.read_text(encoding="utf-8")
    logs_section = policy.split("[scopes.chatgpt-logs]", 1)[1].split(
        "[scopes.chatgpt-actions]", 1
    )[0]

    assert '"help"' in logs_section
    assert '"log.sources"' in logs_section


def test_watchtower_deploy_accepts_wire_as_gway_extension() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Verify Watchtower Wire extension" in workflow
    assert "systemctl is-active --quiet gway-wire-enroll.service" in workflow
    assert "/usr/local/bin/gway log sources" in workflow
    assert "grep -F 'gway/wire-enroll'" in workflow
    assert "https://register.arthexis.com/health" in workflow
    assert "gway wire server check --domain register.arthexis.com" in workflow
