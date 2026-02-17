"""Regression coverage for host loading from generated site configs."""

from __future__ import annotations

import json

from config.settings_helpers import load_site_config_allowed_hosts, normalize_site_host


def test_normalize_site_host_accepts_urls_and_host_port() -> None:
    """normalize_site_host should accept ws/wss URLs and host:port values."""

    assert normalize_site_host("wss://porsche-abb-1.gelectriic.com/") == "porsche-abb-1.gelectriic.com"
    assert normalize_site_host("example.test:8443") == "example.test"


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
