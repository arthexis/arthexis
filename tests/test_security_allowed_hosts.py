"""Regression tests for security host allow-list defaults."""

import pytest

from config.settings_helpers import load_site_config_allowed_hosts, normalize_site_host


pytestmark = [pytest.mark.regression]


def test_normalize_site_host_rejects_wildcard_hosts() -> None:
    """Wildcard site host entries should be rejected before ALLOWED_HOSTS use."""

    assert normalize_site_host("*.example.com") == ""
    assert normalize_site_host("https://*.example.com/path") == ""


def test_load_site_config_allowed_hosts_skips_wildcards(tmp_path) -> None:
    """Managed site config should ignore wildcard domains from generated JSON."""

    generated = tmp_path / "scripts" / "generated"
    generated.mkdir(parents=True)
    (generated / "nginx-sites.json").write_text(
        """
        [
          {"domain": "tenant.example.com"},
          {"domain": "*.tenant.example.com"}
        ]
        """,
        encoding="utf-8",
    )

    assert load_site_config_allowed_hosts(tmp_path) == ["tenant.example.com"]
