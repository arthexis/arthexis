from pathlib import Path

from gway.recipe import load_recipe


DEPLOY_FILES = (
    Path("deploy/watchtower.rx"),
    Path("deploy/arthexis.rx"),
    Path("deploy/remote.rx"),
    Path("deploy/remote-dns.rx"),
    Path("deploy/remote-expose.rx"),
    Path("deploy/mcp-server.rx"),
    Path(".github/workflows/watchtower-deploy.yml"),
)


def _commands(path: Path) -> list[str]:
    commands, _ = load_recipe(path)
    return [" ".join(str(token) for token in command["tokens"]) for command in commands]


def test_recurring_remote_deployment_contains_no_credential_issuance() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in DEPLOY_FILES)

    for forbidden in (
        "security token create",
        "oauth token create",
        "gwt_",
        "gwa_",
    ):
        assert forbidden not in text.lower()


def test_remote_policy_application_is_convergent_and_singular() -> None:
    commands = _commands(Path("deploy/remote.rx"))

    assert commands.count("security scope apply deploy/mcp-scopes.toml") == 1
    assert commands.count("security scope show chatgpt-logs") == 1


def test_remote_service_reconciliation_uses_stable_install_restart_pairs() -> None:
    commands = _commands(Path("deploy/remote.rx"))

    mcp = [command for command in commands if "mcp-server" in command]
    auth = [command for command in commands if "remote serve" in command]

    assert len(mcp) == 2
    assert mcp[0].startswith("service install ")
    assert mcp[1].startswith("service restart ")

    assert len(auth) == 2
    assert auth[0].startswith("service install ")
    assert auth[1].startswith("service restart ")


def test_recurring_public_deployment_never_recreates_dns() -> None:
    commands = _commands(Path("deploy/remote-expose.rx"))

    assert not any(command.startswith("dns create") for command in commands)
    assert commands[0].startswith("dns ready remote.arthexis.com")


def test_dns_bootstrap_is_one_time_and_separate() -> None:
    commands = _commands(Path("deploy/remote-dns.rx"))

    assert len(commands) == 1
    assert commands[0].startswith("dns create remote.arthexis.com")
