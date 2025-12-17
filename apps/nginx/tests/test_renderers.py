from pathlib import Path

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
    config = generate_primary_config("public", 8080)

    assert "return 301 https://$host$request_uri;" in config
    assert "ssl_certificate" in config
    assert "proxy_pass http://127.0.0.1:8080" in config


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
    changed = apply_site_entries(staging, "public", 8888, dest)

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
