"""Tests for host normalization sourced from site configuration."""

from __future__ import annotations

import json

from config.settings_helpers import load_site_config_allowed_hosts, normalize_site_host


def test_normalize_site_host_rejects_wildcards():
    """Wildcard hostnames are rejected to preserve host header validation."""

    assert normalize_site_host("*") == ""
    assert normalize_site_host("https://*.example.com") == ""


def test_load_site_config_allowed_hosts_ignores_wildcard_domains(tmp_path):
    """Wildcard domains in generated config are not added to allowed hosts."""

    config_path = tmp_path / "scripts" / "generated" / "nginx-sites.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            [
                {"domain": "*", "require_https": True},
                {"domain": "https://good.example.com", "require_https": False},
                {"domain": "*.bad.example.com", "require_https": True},
                {"domain": "good.example.com", "require_https": True},
            ]
        ),
        encoding="utf-8",
    )

    assert load_site_config_allowed_hosts(tmp_path) == ["good.example.com"]
