from pathlib import Path
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


def test_base_watchtower_stage_excludes_remote_provisioning() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Provision Watchtower remote policy and services" not in workflow
    assert "/usr/local/bin/gway ./deploy/remote.rx" not in workflow
    assert "default: arthexis" in workflow


def test_watchtower_deploy_accepts_remote_as_manual_gway_extension() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "          - remote" in workflow
    assert "- name: Preflight Watchtower Remote stage" in workflow
    assert "- name: Converge Watchtower Remote stage" in workflow
    assert "- name: Verify Watchtower Remote stage" in workflow
    assert "gway --recipe deploy/remote.rx" in workflow
    assert "gway --recipe deploy/remote-expose.rx" in workflow
    assert "systemctl is-active --quiet gway-mcp-server.service" in workflow
    assert "systemctl is-active --quiet gway-remote-auth.service" in workflow
    assert "verify_remote_deployment.py local" in workflow
    assert "verify_remote_deployment.py public" in workflow

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
    assert 'exec /var/lib/gway/venv/bin/python -m gway "$@"' in workflow
    assert 'install -m 0755 "${launcher}" /usr/local/bin/gway' in workflow
    assert "/usr/local/bin/gway help security oauth client create" in workflow
    assert "/usr/local/bin/gway security oauth client list" in workflow


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


def test_watchtower_deploy_accepts_wire_as_manual_gway_extension() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "- name: Preflight Watchtower Wire stage" in workflow
    assert "- name: Converge Watchtower Wire stage" in workflow
    assert "- name: Verify Watchtower Wire stage" in workflow
    assert "default: arthexis" in workflow
    assert "gway -e wire watchtower" not in workflow
    assert "test -f /var/lib/gway/venv/share/gway/sampler/wire/watchtower.rx" in workflow
    assert "gway --json resolve wire watchtower" in workflow
    assert "sampler/wire/watchtower.rx" in workflow
    assert "gway wire watchtower" in workflow
    assert "systemctl is-active --quiet gway-wire-enroll.service" in workflow
    assert "gway-wireguard-enroll.service" in workflow
    assert "/usr/local/bin/gway log sources" in workflow
    assert "https://register.arthexis.com/health" in workflow
    assert "gway wire server check --domain register.arthexis.com" in workflow

def test_watchtower_validates_relocated_gway_with_module_entrypoint() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "/var/lib/gway/venv/bin/python -m gway --help" in workflow
    assert "/var/lib/gway/venv/bin/gway --help" not in workflow


def test_watchtower_quarantines_only_incomplete_inactive_product_target() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "! sudo -n test -f /opt/arthexis/pyproject.toml" in workflow
    assert '[[ "${exec_start}" == *"/opt/arthexis/"* ]]' in workflow
    assert "Refusing to quarantine active incomplete /opt/arthexis runtime" in workflow
    assert 'sudo -n mv /opt/arthexis "${stale_product}"' in workflow
    assert 'sudo -n mv "${stale_product}" /opt/arthexis' in workflow


def test_watchtower_service_transition_tolerates_absent_legacy_web_and_diagnoses_failure() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "legacy_web_stop=already_absent_or_inactive" in workflow
    assert "service_diagnostics()" in workflow
    assert "arthexis_service_install=failed" in workflow
    assert "arthexis_service_restart=failed" in workflow
    assert "arthexis_ready=failed" in workflow
    assert "journalctl -u arthexis-arthexis.com.service -n 80" in workflow


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


def test_watchtower_readiness_uses_arthexis_product_runtime() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "/opt/arthexis/.venv/bin/python -c" in workflow
    assert "from arthexis.ready import local" in workflow
    assert "exec /usr/local/bin/gway ./deploy/ready.rx" not in workflow


def test_remote_verifier_runs_only_in_remote_stage() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "if: env.WATCHTOWER_STAGE == 'remote'" in workflow
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


def test_watchtower_rollover_reconciles_after_unchanged_accepted_retry() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    step = workflow.split("- name: Open next-version PR after accepted deployment", 1)[1]
    step = step.split("- name: Restore previous Gway runtime after failed deployment", 1)[0]
    assert "if: env.WATCHTOWER_STAGE == 'arthexis'" in step
    assert "ARTHEXIS_CHANGED" not in step
    assert 'gh pr list --repo "$GITHUB_REPOSITORY"' in step
    assert 'gh pr create --repo "$GITHUB_REPOSITORY"' in step


def test_watchtower_next_version_worktree_cleanup_is_retry_safe() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "git worktree prune" in workflow
    assert 'git -C "$GITHUB_WORKSPACE" worktree remove --force "$work"' in workflow
    assert 'trap cleanup EXIT' in workflow


def test_watchtower_rollover_push_uses_release_token_ephemerally() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'GH_TOKEN: ${{ secrets.RELEASE_AUTOMATION_TOKEN }}' in workflow
    assert "printf 'x-access-token:%s' \"$GH_TOKEN\" | base64 -w0" in workflow
    assert 'http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth_header}' in workflow
    assert 'push --force-with-lease origin "$branch"' in workflow
    push_line = next(
        line for line in workflow.splitlines()
        if 'http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth_header}' in line
    )
    assert push_line.endswith("\\")
    assert not push_line.endswith("\\\\")
    assert "https://$GH_TOKEN@" not in workflow


def test_watchtower_rollover_push_command_executes_against_local_remote(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "CI"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "ci@example.invalid"], check=True)
    (work / "VERSION").write_text("2.0.2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "VERSION"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(remote)], check=True)

    command = """
set -Eeuo pipefail
branch=release/next-arthexis
auth_header="$(printf 'x-access-token:%s' "test-token" | base64 -w0)"
git checkout -B "$branch"
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth_header}" \\
  push --force-with-lease origin "$branch"
"""
    subprocess.run(["bash", "-c", command], cwd=work, check=True, capture_output=True, text=True)
    remote_branch = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/release/next-arthexis"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    local_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_branch == local_head

def test_watchtower_coalesces_deploys_until_cross_repo_pr_queue_drains() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "queue-gate:" in workflow
    assert "name: Coalesce active PR queue" in workflow
    assert "for repository in arthexis/arthexis arthexis/gway; do" in workflow
    assert "pulls?state=open&per_page=100" in workflow
    assert 'index("on-hold")' in workflow
    assert 'echo "deploy=false" >> "$GITHUB_OUTPUT"' in workflow
    assert 'echo "deploy=true" >> "$GITHUB_OUTPUT"' in workflow
    assert "needs: [classify, queue-gate]" in workflow
    assert "needs.queue-gate.outputs.deploy == 'true'" in workflow


def test_watchtower_queue_gate_runs_before_self_hosted_deploy() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate = workflow.index("  queue-gate:")
    deploy = workflow.index("\n  deploy:\n    name: Watchtower Deploy\n", gate)
    runner = workflow.index("runs-on: [self-hosted, Linux, X64, arthexis-ci]", deploy)

    assert gate < deploy < runner
    assert "runs-on: ubuntu-latest" in workflow[gate:deploy]
