from pathlib import Path
import pytest
import subprocess


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




def test_watchtower_mcp_policy_has_read_only_remote_scopes() -> None:
    policy = POLICY.read_text(encoding="utf-8")

    assert "[scopes.chatgpt-logs]" in policy
    assert "[scopes.chatgpt-actions]" in policy
    assert '"help"' in policy
    assert '"log.sources"' in policy
    assert '"log.read"' in policy
    assert '"log.tail"' in policy
    assert '"log.search"' in policy
    logs_section = policy.split("[scopes.chatgpt-logs]", 1)[1].split(
        "[scopes.chatgpt-actions]", 1
    )[0]
    actions_section = policy.split("[scopes.chatgpt-actions]", 1)[1].split(
        "[scopes.full-access]", 1
    )[0]
    for section in (logs_section, actions_section):
        assert "environment = []" in section
        for forbidden in ("clear", "service.", "security.", "__all__"):
            assert forbidden not in section

    full_access = policy.split("[scopes.full-access]", 1)[1]
    assert 'operations = ["__all__"]' in full_access
    assert 'environment = ["__all__"]' in full_access


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


@pytest.mark.workflow
def test_base_watchtower_stage_keeps_remote_provisioning_as_a_phase() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "default: arthexis" in workflow
    assert "- name: Preflight Watchtower Remote stage" in workflow
    assert "- name: Converge Watchtower Remote stage" in workflow
    assert "- name: Verify Watchtower Remote stage" in workflow
    assert "env.WATCHTOWER_STAGE == 'arthexis'" in workflow


@pytest.mark.workflow
def test_watchtower_deploy_accepts_remote_only_as_repair_stage() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "          - remote-only" in workflow
    assert 'grep -Fxq remote-only <<<"$labels"' in workflow
    assert "- name: Preflight Watchtower Remote stage" in workflow
    assert "- name: Converge Watchtower Remote stage" in workflow
    assert "- name: Verify Watchtower Remote stage" in workflow
    assert "gway --recipe deploy/remote.rx" in workflow
    assert "gway --recipe deploy/remote-expose.rx" in workflow
    assert "systemctl is-active --quiet gway-mcp-server.service" in workflow
    assert "systemctl is-active --quiet gway-remote-auth.service" in workflow
    assert "verify_remote_deployment.py local" in workflow
    assert "verify_remote_deployment.py public" in workflow

@pytest.mark.workflow
def test_watchtower_workflow_no_longer_reimplements_mcp_service_setup() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "from gway.sampler import root" not in workflow
    assert '"mcp" / "server.rx"' not in workflow
    assert "service install --backend systemd --system --name mcp-server" not in workflow
    assert "security token create" not in workflow


def test_remote_recipe_uses_semantic_gway_cache_root() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    remote = REMOTE.read_text(encoding="utf-8")
    assert "[tool.gway.variables]" in project
    assert 'cache_dir = "/var/lib/gway/cache"' in project
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



@pytest.mark.workflow
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
        "if set(tools) != expected_tools:",
        'expected_tools={"query"}',
        'expected_tools={"gway", "query"}',
        'annotations.get("readOnlyHint") is not True',
        'annotations.get("readOnlyHint") is not False',
        '"name": "query"',
        'query_command="log sources"',
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


@pytest.mark.workflow
def test_watchtower_installs_canonical_admin_gway_launcher() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Install canonical Watchtower Gway admin launcher" in workflow
    assert "export GWAY_CACHE_DIR=/var/lib/gway/cache" in workflow
    assert "cd /var/lib/gway/projects/arthexis" not in workflow
    assert 'exec /var/lib/gway/venv/bin/python -m gway "$@"' in workflow
    assert 'install -m 0755 "${launcher}" /usr/local/bin/gway' in workflow
    assert "/usr/local/bin/gway help security oauth client create" in workflow
    assert "/usr/local/bin/gway security oauth client list" in workflow


@pytest.mark.workflow
def test_arthexis_product_runtime_is_separate_from_gway() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "test -f /opt/arthexis/pyproject.toml" in workflow
    assert "test -f /opt/arthexis/install.sh" in workflow
    assert "/opt/arthexis/.venv/bin/python -m pip install \"gway" not in workflow
    assert "Arthexis product venv must not contain GWAY" in workflow
    assert "/var/lib/gway/venv/bin/python -m gway" in workflow
    assert "service=active_independent_of_gway" in workflow
    assert "ExecStart must not depend on GWAY" in workflow


def test_chatgpt_logs_scope_includes_help_for_existing_tokens() -> None:
    policy = POLICY.read_text(encoding="utf-8")
    logs_section = policy.split("[scopes.chatgpt-logs]", 1)[1].split(
        "[scopes.chatgpt-actions]", 1
    )[0]

    assert '"help"' in logs_section
    assert '"log.sources"' in logs_section



@pytest.mark.workflow
def test_watchtower_validates_relocated_gway_with_module_entrypoint() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "/var/lib/gway/venv/bin/python -m gway --help" in workflow
    assert "/var/lib/gway/venv/bin/gway --help" not in workflow


@pytest.mark.workflow
def test_watchtower_quarantines_only_incomplete_inactive_product_target() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "! sudo -n test -f /opt/arthexis/pyproject.toml" in workflow
    assert '[[ "${exec_start}" == *"/opt/arthexis/"* ]]' in workflow
    assert "Refusing to quarantine active incomplete /opt/arthexis runtime" in workflow
    assert 'sudo -n mv /opt/arthexis "${stale_product}"' in workflow
    assert 'sudo -n mv "${stale_product}" /opt/arthexis' in workflow


@pytest.mark.workflow
def test_watchtower_service_transition_tolerates_absent_legacy_web_and_diagnoses_failure() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "legacy_web_stop=already_absent_or_inactive" in workflow
    assert "service_diagnostics()" in workflow
    assert "arthexis_service_install=failed" in workflow
    assert "arthexis_service_restart=failed" in workflow
    assert "arthexis_ready=failed" in workflow
    assert "journalctl -u arthexis-arthexis.com.service -n 80" in workflow


@pytest.mark.workflow
def test_arthexis_systemd_service_uses_accepted_build_compatible_server_command() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    compatible = (
        "/opt/arthexis/.venv/bin/python -c "
        "'from arthexis.server import main; "
        'main(host="127.0.0.1", port=8888, data_dir="/var/lib/arthexis", '
        'allowed_hosts="arthexis.com")\''
    )
    assert (
        f"service install --backend systemd --system --name arthexis.com -- {compatible}"
        in workflow
    )
    assert (
        f"service restart --system --name arthexis.com --timeout 40 -- {compatible}"
        in workflow
    )
    assert "accepted-build-compatible server callable" in workflow
    assert "/opt/arthexis/.venv/bin/python -m arthexis.server" not in workflow
    assert "/opt/arthexis/.venv/bin/python -m gway" not in workflow


@pytest.mark.workflow
def test_watchtower_readiness_uses_arthexis_product_runtime() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "/opt/arthexis/.venv/bin/python -c" in workflow
    assert "from arthexis.ready import local" in workflow
    assert "exec /usr/local/bin/gway ./deploy/ready.rx" not in workflow


@pytest.mark.workflow
def test_remote_verifier_runs_in_full_and_remote_only_stages() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "env.WATCHTOWER_STAGE == 'arthexis'" in workflow
    assert "env.WATCHTOWER_STAGE == 'remote-only'" in workflow
    assert "verify_remote_deployment.py local" in workflow
    assert "verify_remote_deployment.py public" in workflow

def test_ready_recipe_executes_product_runtime_externally() -> None:
    recipe = Path("deploy/ready.rx").read_text(encoding="utf-8")

    assert (
        "ingest /opt/arthexis/.venv/bin/python --kind proc --aka arthexis-python"
        in recipe
    )
    assert "arthexis-python -m arthexis.ready --local" in recipe
    assert "/opt/arthexis/.venv/bin/ready" not in recipe
    assert "\nready --local\n" not in recipe


@pytest.mark.workflow
def test_watchtower_restores_previous_gway_runtime_on_failed_deploy() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'echo "GWAY_RUNTIME_ROLLBACK_AVAILABLE=true" >> "$GITHUB_ENV"' in workflow
    assert 'echo "GWAY_RUNTIME_SWAPPED=true" >> "$GITHUB_ENV"' in workflow
    assert "- name: Restore previous Gway runtime after failed deployment" in workflow
    assert 'echo "WATCHTOWER_DEPLOYMENT_ACCEPTED=true" >> "$GITHUB_ENV"' in workflow
    assert (
        "failure() && env.WATCHTOWER_STAGE == 'arthexis' "
        "&& env.GWAY_RUNTIME_SWAPPED == 'true' "
        "&& env.WATCHTOWER_DEPLOYMENT_ACCEPTED != 'true'"
    ) in workflow
    assert "mv /var/lib/gway/venv /var/lib/gway/venv.failed" in workflow
    assert "mv /var/lib/gway/venv.previous /var/lib/gway/venv" in workflow
    assert "systemctl restart gway-mcp-server.service" in workflow
    assert "systemctl restart gway-remote-auth.service" in workflow
    assert "systemctl is-active --quiet gway-mcp-server.service" in workflow
    assert "systemctl is-active --quiet gway-remote-auth.service" in workflow
    assert 'for port in 8000 8001; do' in workflow
    assert 'gway_runtime_rollback=restored' in workflow





@pytest.mark.workflow
def test_watchtower_queue_gate_runs_before_self_hosted_deploy() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate = workflow.index("  queue-gate:")
    deploy = workflow.index("\n  deploy:\n    name: Watchtower Deploy\n", gate)
    runner = workflow.index("runs-on: [self-hosted, Linux, X64, arthexis-ci]", deploy)

    assert gate < deploy < runner
    assert "runs-on: ubuntu-latest" in workflow[gate:deploy]


@pytest.mark.workflow
def test_full_ci_does_not_rerun_for_label_only_changes() -> None:
    workflow_paths = (
        Path(".github/workflows/python-quality.yml"),
        Path(".github/workflows/python-package.yml"),
        Path(".github/workflows/python-compatibility.yml"),
        Path(".github/workflows/secret-scan.yml"),
    )

    for path in workflow_paths:
        workflow = path.read_text(encoding="utf-8")
        assert "types: [opened, synchronize, reopened]" in workflow
        assert "labeled" not in workflow.split("workflow_dispatch:", 1)[0]
        assert "unlabeled" not in workflow.split("workflow_dispatch:", 1)[0]



@pytest.mark.workflow
def test_gway_dispatch_coalesces_with_current_arthexis_main() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    dispatch = workflow.split('elif [[ "$EVENT_NAME" == "repository_dispatch" ]]; then', 1)[1]
    dispatch = dispatch.split('else\n            requested_arthexis=', 1)[0]

    assert 'arthexis_sha="$current_arthexis"' in dispatch
    assert 'arthexis_sha="${previous_arthexis:-$current_arthexis}"' not in dispatch
    assert 'gway_sha="$current_gway"' in dispatch
    assert 'source="gway-coalesced"' in dispatch or 'source="gway"' in dispatch


