from pathlib import Path


def test_watchtower_recipe_uses_public_exposure_bundle() -> None:
    recipe = Path("deploy/watchtower.rx").read_text(encoding="utf-8")

    assert "recipe web/expose" in recipe
    assert "--site arthexis.com" in recipe
    assert "--domain arthexis.com" in recipe
    assert "--host 127.0.0.1" in recipe
    assert "--port 8888" in recipe
    assert "--email [email]" in recipe


def test_watchtower_recipe_checks_local_and_public_health() -> None:
    recipe = Path("deploy/watchtower.rx").read_text(encoding="utf-8")

    assert 'Host: [domain|arthexis.com]' in recipe
    assert "http://[host|127.0.0.1]:[port|8888][health_path|/health/]" in recipe
    assert "https://[domain|arthexis.com][health_path|/health/]" in recipe
