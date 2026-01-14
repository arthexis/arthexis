from pathlib import Path

from types import SimpleNamespace

from apps.nginx import config_utils
from apps.nginx.renderers import (
    apply_site_entries,
    generate_primary_config,
    generate_site_entries_content,
)


def test_generate_primary_config_internal_mode():
    config = generate_primary_config("internal", 8080)

    assert "proxy_pass http://127.0.0.1:8080" in config
    assert "ssl_certificate" not in config


def test_generate_primary_config_public_mode():
    config = generate_primary_config("public", 8080, https_enabled=True)

    assert "return 301 https://$host$request_uri;" in config
    assert "ssl_certificate" in config
    assert "proxy_pass http://127.0.0.1:8080" in config


def test_generate_primary_config_external_websockets_toggle():
    config = generate_primary_config("internal", 8080, external_websockets=True)

    assert config_utils.WEBSOCKET_MAP_DIRECTIVE in config
    assert config_utils.WEBSOCKET_CONNECTION_HEADER in config
    assert config_utils.WEBSOCKET_READ_TIMEOUT in config

    disabled = generate_primary_config("internal", 8080, external_websockets=False)

    assert config_utils.WEBSOCKET_MAP_DIRECTIVE not in disabled
    assert config_utils.WEBSOCKET_CONNECTION_HEADER not in disabled


def test_generate_primary_config_with_secondary_backend():
    secondary = SimpleNamespace(name="Blue", port=9090)

    config = generate_primary_config(
        "internal", 8080, secondary_instance=secondary, external_websockets=True
    )

    assert "upstream arthexis-blue-pool" in config
    assert "server 127.0.0.1:8080;" in config
    assert "server 127.0.0.1:9090 backup;" in config
    assert "proxy_pass http://arthexis-blue-pool" in config
    assert config_utils.WEBSOCKET_MAP_DIRECTIVE in config


def test_apply_site_entries(tmp_path: Path):
    staging = tmp_path / "sites.json"
    staging.write_text(
        """
        [
          {"domain": "example.com", "require_https": true},
          {"domain": "example.com", "require_https": false},
          {"domain": "demo.arthexis.com", "require_https": false}
        ]
        """,
        encoding="utf-8",
    )

    dest = tmp_path / "sites.conf"
    changed = apply_site_entries(staging, "public", 8888, dest, https_enabled=True)

    assert changed is True
    content = dest.read_text(encoding="utf-8")
    assert "Managed site for example.com" in content
    assert "return 301 https://$host$request_uri;" in content
    assert "demo.arthexis.com" in content


def test_generate_site_entries_content_matches_written_file(tmp_path: Path):
    staging = tmp_path / "sites.json"
    staging.write_text(
        """
        [{"domain": "preview.example.com", "require_https": false}]
        """,
        encoding="utf-8",
    )

    dest = tmp_path / "sites.conf"

    preview_content = generate_site_entries_content(staging, "internal", 8080)
    apply_site_entries(staging, "internal", 8080, dest)

    assert dest.read_text(encoding="utf-8") == preview_content


def test_generate_site_entries_content_uses_proxy_target(tmp_path: Path):
    staging = tmp_path / "sites.json"
    staging.write_text('[{"domain": "proxy.example.com", "require_https": false}]', encoding="utf-8")

    content = generate_site_entries_content(
        staging, "public", 8080, proxy_target="arthexis-blue"
    )

    assert "proxy_pass http://arthexis-blue" in content


def test_generate_site_entries_content_expands_subdomains(tmp_path: Path):
    staging = tmp_path / "sites.json"
    staging.write_text('[{"domain": "example.com", "require_https": false}]', encoding="utf-8")

    content = generate_site_entries_content(
        staging,
        "public",
        8080,
        https_enabled=True,
        subdomain_prefixes=["api", "admin"],
    )

    assert "server_name example.com api.example.com admin.example.com;" in content


def test_ssl_directives_omitted_when_assets_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config_utils, "SSL_OPTIONS_PATH", tmp_path / "missing-options.conf")
    monkeypatch.setattr(config_utils, "BUNDLED_SSL_OPTIONS_PATH", tmp_path / "missing-bundled-options.conf")
    monkeypatch.setattr(config_utils, "SSL_DHPARAM_PATH", tmp_path / "missing-dhparam.pem")
    monkeypatch.setattr(config_utils, "BUNDLED_SSL_DHPARAM_PATH", tmp_path / "missing-bundled-dhparam.pem")

    config = config_utils.https_proxy_server("example.test", 8443)

    assert "ssl_certificate" in config
    assert "include /" not in config
    assert "ssl_dhparam" not in config


def test_ssl_directives_use_bundled_fallback(monkeypatch, tmp_path: Path):
    missing_options = tmp_path / "missing-options.conf"
    bundled_options = tmp_path / "fallback-options.conf"
    bundled_options.write_text("ssl_session_cache off;", encoding="utf-8")
    missing_dhparam = tmp_path / "missing-dhparam.pem"
    bundled_dhparam = tmp_path / "fallback-dhparam.pem"
    bundled_dhparam.write_text("test-dhparam", encoding="utf-8")

    monkeypatch.setattr(config_utils, "SSL_OPTIONS_PATH", missing_options)
    monkeypatch.setattr(config_utils, "BUNDLED_SSL_OPTIONS_PATH", bundled_options)
    monkeypatch.setattr(config_utils, "SSL_DHPARAM_PATH", missing_dhparam)
    monkeypatch.setattr(config_utils, "BUNDLED_SSL_DHPARAM_PATH", bundled_dhparam)

    config = config_utils.default_reject_server(https=True)

    assert f"include {bundled_options}" in config
    assert f"ssl_dhparam {bundled_dhparam}" in config
