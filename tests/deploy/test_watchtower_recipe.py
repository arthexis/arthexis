from pathlib import Path

from gway.recipe import load_recipe


def _commands(path: str) -> list[str]:
    commands, _ = load_recipe(Path(path))
    return [" ".join(str(token) for token in command["tokens"]) for command in commands]


def test_watchtower_recipe_is_top_level_public_composition() -> None:
    recipe = Path("deploy/watchtower.rx").read_text(encoding="utf-8")

    assert "./arthexis.rx" in recipe
    assert "./remote-expose.rx" in recipe
    assert "recipe web/expose" not in recipe
    assert "recipe web/remote" not in recipe


def test_arthexis_recipe_uses_public_exposure_bundle() -> None:
    recipe = Path("deploy/arthexis.rx").read_text(encoding="utf-8")

    assert "recipe web/expose" in recipe
    assert "--site arthexis.com" in recipe
    assert "--domain arthexis.com" in recipe
    assert "--host 127.0.0.1" in recipe
    assert "--port 8888" in recipe
    assert "--email [email]" in recipe


def test_arthexis_recipe_checks_local_and_public_health() -> None:
    recipe = Path("deploy/arthexis.rx").read_text(encoding="utf-8")

    assert "./ready.rx" in recipe
    assert "https://[domain|arthexis.com][health_path|/health/]" in recipe


def test_remote_service_recipe_uses_loopback_and_no_production_credentials() -> None:
    recipe = Path("deploy/remote.rx").read_text(encoding="utf-8")

    assert "https://remote.arthexis.com" in recipe
    assert "chatgpt-logs" in recipe
    assert "127.0.0.1" in recipe
    assert "security token create" not in recipe
    assert "oauth token" not in recipe.lower()


def test_remote_dns_bootstrap_is_separate_from_recurring_exposure() -> None:
    dns_commands = _commands("deploy/remote-dns.rx")
    expose_commands = _commands("deploy/remote-expose.rx")

    assert len(dns_commands) == 1
    assert dns_commands[0].startswith("dns create remote.arthexis.com")
    assert "--value [public_ipv4]" in dns_commands[0]
    assert "--backend [dns_backend|godaddy]" in dns_commands[0]
    assert "--zone arthexis.com" in dns_commands[0]

    assert not any(command.startswith("dns create") for command in expose_commands)
    assert expose_commands[0].startswith("dns ready remote.arthexis.com")
    assert "--value [public_ipv4]" in expose_commands[0]
    assert "--backend [dns_backend|godaddy]" in expose_commands[0]
    assert "--zone arthexis.com" in expose_commands[0]
    assert expose_commands[1].startswith("repeat ")
    expose = expose_commands[2]

    assert expose.startswith("recipe web/remote")
    assert "--site remote.arthexis.com" in expose
    assert "--domain remote.arthexis.com" in expose
    assert "--mcp-host 127.0.0.1" in expose
    assert "--mcp-port 8000" in expose
    assert "--auth-host 127.0.0.1" in expose
    assert "--auth-port 8001" in expose
    assert "--email [email]" in expose


def test_remote_exposure_does_not_reimplement_nginx_or_certbot() -> None:
    recipe = Path("deploy/remote-expose.rx").read_text(encoding="utf-8")

    assert "dns create" not in recipe
    assert "nginx " not in recipe
    assert "certbot " not in recipe
    assert "render " not in recipe
