import pytest

from scripts.helpers.nginx_config import (
    http_proxy_server,
    http_redirect_server,
    https_proxy_server,
    maintenance_block,
)


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


def test_maintenance_block_adds_custom_error_pages():
    block = maintenance_block()
    assert "error_page 404 /maintenance/404.html;" in block
    assert "error_page 500 502 503 504 /maintenance/index.html;" in block
    assert "location = /maintenance/404.html" in block
    assert "location = /maintenance/index.html" in block
    assert "location /maintenance/ {" in block
