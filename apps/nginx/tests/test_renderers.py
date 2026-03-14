from pathlib import Path

import pytest

from apps.nginx import config_utils
from apps.nginx.renderers import (
    generate_primary_config,
    generate_site_entries_content,
    generate_unified_config,
)









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



def test_generate_unified_config_includes_managed_sites(tmp_path: Path):
    """Unified nginx config should include primary and managed site blocks in one file."""

    staging = tmp_path / "sites.json"
    staging.write_text('[{"domain": "tenant.example.com", "require_https": true}]', encoding="utf-8")

    content = generate_unified_config(
        "public",
        8080,
        https_enabled=True,
        site_config_path=staging,
    )

    assert "server_name arthexis.com *.arthexis.com;" in content
    assert "Managed site for tenant.example.com" in content
