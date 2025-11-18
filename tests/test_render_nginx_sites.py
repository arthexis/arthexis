import json

import pytest

from scripts.helpers.render_nginx_sites import apply_sites


pytestmark = [pytest.mark.feature("nginx-server")]


def test_apply_sites_generates_http_config(tmp_path):
    config_path = tmp_path / "sites.json"
    config_path.write_text(
        json.dumps([
            {"domain": "example.com", "require_https": False},
        ])
    )

    dest_path = tmp_path / "conf" / "arthexis-sites.conf"
    changed = apply_sites(config_path, "internal", 8888, dest_path)
    assert changed is True

    conf = dest_path.read_text()
    assert "listen 80;" in conf
    assert "proxy_pass http://127.0.0.1:8888/" in conf
    assert "listen 443" not in conf
    assert "proxy_set_header Host $http_host;" in conf


def test_apply_sites_generates_https_blocks(tmp_path):
    config_path = tmp_path / "sites.json"
    config_path.write_text(
        json.dumps([
            {"domain": "secure.test", "require_https": True},
        ])
    )

    dest_path = tmp_path / "conf" / "arthexis-sites.conf"
    changed = apply_sites(config_path, "public", 8888, dest_path)
    assert changed is True

    conf = dest_path.read_text()
    assert "return 301 https://$host$request_uri;" in conf
    assert "listen 443 ssl;" in conf
    assert "proxy_pass http://127.0.0.1:8888/" in conf
    assert "proxy_set_header Host $http_host;" in conf


def test_apply_sites_overwrites_empty_config(tmp_path):
    dest_path = tmp_path / "conf" / "arthexis-sites.conf"
    dest_path.parent.mkdir()
    dest_path.write_text("stale", encoding="utf-8")

    config_path = tmp_path / "sites.json"
    config_path.write_text("[]")

    changed = apply_sites(config_path, "internal", 8888, dest_path)
    assert changed is True
    content = dest_path.read_text()
    assert "No managed sites configured" in content


def test_apply_sites_idempotent_when_unchanged(tmp_path):
    config_path = tmp_path / "sites.json"
    config_path.write_text(
        json.dumps([
            {"domain": "example.com", "require_https": False},
        ])
    )

    dest_path = tmp_path / "conf" / "arthexis-sites.conf"
    apply_sites(config_path, "internal", 8888, dest_path)
    first = dest_path.read_text()

    changed = apply_sites(config_path, "internal", 8888, dest_path)
    assert changed is False
    second = dest_path.read_text()
    assert first == second
