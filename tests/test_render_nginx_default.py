import pytest

from scripts.helpers.render_nginx_default import generate_config


pytestmark = [pytest.mark.feature("nginx-server")]


def test_generate_internal_config_uses_all_listeners(tmp_path):
    config = generate_config("internal", 8888)
    for listen in ["0.0.0.0:80", "[::]:8080"]:
        assert f"listen {listen};" in config
    assert "server_name _;" in config
    assert "listen 443" not in config
    assert "proxy_pass http://127.0.0.1:8888;" in config


def test_generate_public_config_includes_https_block():
    config = generate_config("public", 9000)
    assert "server_name arthexis.com *.arthexis.com _;" in config
    assert "server_name arthexis.com *.arthexis.com;" in config
    assert "listen 443 ssl;" in config
    assert "proxy_pass http://127.0.0.1:9000;" in config


def test_generate_config_allows_custom_server_names():
    config = generate_config(
        "public",
        7000,
        http_server_names="example.org _",
        https_server_names="example.org",
    )
    assert "server_name example.org _;" in config
    assert "server_name example.org;" in config
