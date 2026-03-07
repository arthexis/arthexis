"""Regression coverage for host loading from generated site configs."""

from __future__ import annotations

import json
import threading
import time

from config.settings_helpers import (
    load_site_config_allowed_hosts,
    normalize_site_host,
    resolve_local_fqdn,
    should_probe_postgres,
)

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

def test_should_probe_postgres_false_without_postgres_configuration() -> None:
    """should_probe_postgres should skip probing when PostgreSQL is not configured."""

    env = {"ARTHEXIS_DB_BACKEND": ""}

    assert should_probe_postgres(env) is False

def test_should_probe_postgres_true_with_explicit_postgres_backend() -> None:
    """Explicit postgres backend selection should force probing."""

    env = {"ARTHEXIS_DB_BACKEND": "postgres"}

    assert should_probe_postgres(env) is True

def test_should_probe_postgres_true_with_postgres_env_vars() -> None:
    """Any explicit PostgreSQL connection variable should enable probing."""

    env = {"POSTGRES_HOST": "localhost"}

    assert should_probe_postgres(env) is True
