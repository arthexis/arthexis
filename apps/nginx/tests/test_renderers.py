from pathlib import Path

from apps.nginx.renderers import generate_unified_config


def test_generate_unified_config_includes_managed_sites(tmp_path: Path):
    """Unified nginx config should include primary and managed site blocks in one file."""

    staging = tmp_path / "sites.json"
    staging.write_text(
        '[{"domain": "tenant.example.com", "require_https": true}]',
        encoding="utf-8",
    )

    content = generate_unified_config(
        "public",
        8080,
        https_enabled=True,
        site_config_path=staging,
    )

    assert "server_name arthexis.com *.arthexis.com;" in content
    assert "Managed site for tenant.example.com" in content


def test_generate_unified_config_skips_primary_domain_from_managed_sites(tmp_path: Path):
    """Managed site entries should not duplicate the primary domain server blocks."""

    staging = tmp_path / "sites.json"
    staging.write_text(
        '[{"domain": "arthexis.com", "require_https": true}]',
        encoding="utf-8",
    )

    content = generate_unified_config(
        "public",
        8080,
        https_enabled=True,
        site_config_path=staging,
    )

    assert "Managed site for arthexis.com" not in content
