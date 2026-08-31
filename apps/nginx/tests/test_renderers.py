from __future__ import annotations

from apps.nginx import renderers


def test_generate_primary_config_limits_default_http_listens_to_port_80():
    config = renderers.generate_primary_config(
        mode="public",
        port=8000,
        https_enabled=False,
        include_ipv6=True,
    )

    assert "listen 0.0.0.0:80;" in config
    assert "listen [::]:80;" in config
    assert "listen 0.0.0.0:8000;" not in config
    assert "listen [::]:8000;" not in config
    assert "listen 0.0.0.0:8080;" not in config
    assert "listen [::]:8080;" not in config
    assert "listen 0.0.0.0:8900;" not in config
    assert "listen [::]:8900;" not in config
    assert "location ^~ /media/ {" not in config


def test_generate_primary_config_does_not_serve_media_directly(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_URL = "/media/"

    config = renderers.generate_primary_config(
        mode="public",
        port=8000,
        https_enabled=True,
    )

    assert "location ^~ /media/ {" not in config
    assert f'alias "{settings.MEDIA_ROOT}/";' not in config
    assert "proxy_pass http://127.0.0.1:8000;" in config
