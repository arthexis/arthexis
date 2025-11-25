from pathlib import Path

import pytest

from scripts.helpers.render_nginx_default import generate_config


pytestmark = [pytest.mark.feature("nginx-server")]


def test_generate_internal_config_uses_ipv4_listeners_by_default():
    config = generate_config("internal", 8888)
    assert "listen 0.0.0.0:80;" in config
    assert "listen 0.0.0.0:8080;" in config
    assert "listen 0.0.0.0:8900;" in config
    assert "listen [::]:80;" not in config
    assert "server_name _;" in config
    assert "listen 443" not in config
    assert "proxy_pass http://127.0.0.1:8888;" in config
    assert "proxy_set_header Host $http_host;" in config
    assert "error_page 404 /maintenance/404.html;" in config
    assert "error_page 500 502 503 504 /maintenance/index.html;" in config


def test_generate_public_config_includes_https_block():
    config = generate_config("public", 9000)
    assert "server_name arthexis.com *.arthexis.com _;" in config
    assert "server_name arthexis.com *.arthexis.com;" in config
    assert "listen 443 ssl;" in config
    assert "listen [::]:443 ssl;" not in config
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


def test_generate_config_can_include_ipv6():
    config = generate_config("public", 7777, include_ipv6=True)
    for listen in ("0.0.0.0:80", "0.0.0.0:8080", "0.0.0.0:8900", "[::]:80", "[::]:8080", "[::]:8900"):
        assert f"listen {listen};" in config
    assert "listen [::]:443 ssl;" in config


def test_8900_listener_redirects_pathless_requests_to_simulator():
    config = generate_config("internal", 8888)
    assert "set $simulator_redirect \"\";" in config
    assert "if ($server_port = 8900)" in config
    assert "set $simulator_redirect $uri;" in config
    assert "if ($simulator_redirect = \"/\")" in config
    assert "return 302 /ocpp/evcs/simulator/;" in config


def test_nginx_setup_script_removes_default_site():
    script_path = Path(__file__).resolve().parent.parent / "nginx-setup.sh"
    content = script_path.read_text()
    assert "rm -f /etc/nginx/sites-enabled/default" in content
    assert "rm -f /etc/nginx/sites-available/default" in content
