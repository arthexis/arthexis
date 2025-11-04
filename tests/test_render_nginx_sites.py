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

    dest_dir = tmp_path / "conf"
    changed = apply_sites(config_path, "internal", 8888, dest_dir)
    assert changed is True

    conf = (dest_dir / "arthexis-site-example-com.conf").read_text()
    assert "listen 80;" in conf
    assert "proxy_pass http://127.0.0.1:8888/" in conf
    assert "listen 443" not in conf


def test_apply_sites_generates_https_blocks(tmp_path):
    config_path = tmp_path / "sites.json"
    config_path.write_text(
        json.dumps([
            {"domain": "secure.test", "require_https": True},
        ])
    )

    dest_dir = tmp_path / "conf"
    changed = apply_sites(config_path, "public", 8888, dest_dir)
    assert changed is True

    conf_path = dest_dir / "arthexis-site-secure-test.conf"
    conf = conf_path.read_text()
    assert "return 301 https://$host$request_uri;" in conf
    assert "listen 443 ssl;" in conf
    assert "proxy_pass http://127.0.0.1:8888/" in conf


def test_apply_sites_removes_stale_files(tmp_path):
    dest_dir = tmp_path / "conf"
    dest_dir.mkdir()
    stale = dest_dir / "arthexis-site-old.conf"
    stale.write_text("stale", encoding="utf-8")

    config_path = tmp_path / "sites.json"
    config_path.write_text("[]")

    changed = apply_sites(config_path, "internal", 8888, dest_dir)
    assert changed is True
    assert not stale.exists()


def test_apply_sites_idempotent_when_unchanged(tmp_path):
    config_path = tmp_path / "sites.json"
    config_path.write_text(
        json.dumps([
            {"domain": "example.com", "require_https": False},
        ])
    )

    dest_dir = tmp_path / "conf"
    apply_sites(config_path, "internal", 8888, dest_dir)
    first = (dest_dir / "arthexis-site-example-com.conf").read_text()

    changed = apply_sites(config_path, "internal", 8888, dest_dir)
    assert changed is False
    second = (dest_dir / "arthexis-site-example-com.conf").read_text()
    assert first == second
