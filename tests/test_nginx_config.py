import pytest

from scripts.helpers.nginx_config import (
    default_reject_server,
    http_proxy_server,
    http_redirect_server,
    https_proxy_server,
    maintenance_block,
)
from scripts.helpers.render_nginx_default import generate_config


pytestmark = [pytest.mark.feature("nginx-server")]


def test_http_proxy_server_deduplicates_listens():
    block = http_proxy_server("example.org", 8000, listens=["80", "80", "[::]:80", "80"])
    assert block.count("listen 80;") == 1
    assert block.count("listen [::]:80;") == 1


def test_https_proxy_server_deduplicates_listens():
    block = https_proxy_server("example.org", 8000, listens=["443 ssl", "443 ssl"])
    assert block.count("listen 443 ssl;") == 1


def test_http_redirect_server_deduplicates_listens():
    block = http_redirect_server("example.org", listens=["80", "80"])
    assert block.count("listen 80;") == 1


def test_proxy_block_prefers_normalized_host_header():
    block = http_proxy_server("example.org", 8000)
    assert "proxy_set_header Host $host;" in block
    assert "proxy_set_header Host $http_host;" not in block


def test_default_reject_server_uses_default_listeners():
    block = default_reject_server(["80", "[::]:80"])
    assert "listen 80 default_server;" in block
    assert "listen [::]:80 default_server;" in block
    assert block.endswith("return 444;\n}")


def test_public_config_redirects_and_drops_unknown_hosts():
    config = generate_config("public", 8000)

    assert "return 301 https://$host$request_uri;" in config
    assert "server_name arthexis.com *.arthexis.com;" in config
    assert "listen 0.0.0.0:80 default_server;" in config
    assert "listen 0.0.0.0:443 ssl default_server;" in config
    assert config.count("return 444;") >= 2


def test_maintenance_block_adds_custom_error_pages():
    block = maintenance_block()
    assert "error_page 404 /maintenance/404.html;" in block
    assert "error_page 500 502 503 504 /maintenance/index.html;" in block
    assert "location = /maintenance/404.html" in block
    assert "location = /maintenance/index.html" in block
    assert "location /maintenance/ {" in block
