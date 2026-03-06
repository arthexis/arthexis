"""Regression coverage for host loading from generated site configs."""

from __future__ import annotations

import json

from config.settings_helpers import load_site_config_allowed_hosts


def test_load_site_config_allowed_hosts_reads_generated_file(tmp_path) -> None:
    """load_site_config_allowed_hosts should extract unique normalized domains."""

    generated = tmp_path / "scripts" / "generated"
    generated.mkdir(parents=True)
    payload = [
        {"domain": "wss://porsche-abb-1.gelectriic.com/", "require_https": True},
        {"domain": "Porsche-ABB-1.gelectriic.com", "require_https": True},
        {"domain": "example.test:443", "require_https": False},
    ]
    (generated / "nginx-sites.json").write_text(json.dumps(payload), encoding="utf-8")

    assert load_site_config_allowed_hosts(tmp_path) == [
        "porsche-abb-1.gelectriic.com",
        "example.test",
    ]

