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


def test_normalize_site_host_rejects_malformed_url() -> None:
    """normalize_site_host should return empty for malformed URL inputs."""

    assert normalize_site_host("http://[::1") == ""


def test_resolve_local_fqdn_returns_empty_when_lookup_blocks() -> None:
    """Regression: resolve_local_fqdn should timeout instead of hanging startup."""

    blocker = threading.Event()

    def blocking_resolver(_hostname: str) -> str:
        blocker.wait(1.0)
        return "blocked.example"

    started = time.monotonic()
    result = resolve_local_fqdn("test-host", resolver=blocking_resolver, timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert result == ""
    assert elapsed < 0.5


def test_resolve_local_fqdn_returns_trimmed_value() -> None:
    """resolve_local_fqdn should normalize resolver output."""

    result = resolve_local_fqdn("test-host", resolver=lambda _host: " test-host.local ")

    assert result == "test-host.local"


def test_should_probe_postgres_false_without_postgres_configuration() -> None:
    """should_probe_postgres should skip probing when PostgreSQL is not configured."""

    env = {"ARTHEXIS_DB_BACKEND": ""}

    assert should_probe_postgres(env) is False


def test_should_probe_postgres_true_with_explicit_postgres_backend() -> None:
    """Explicit postgres backend selection should force probing."""

    env = {"ARTHEXIS_DB_BACKEND": "postgres"}

    assert should_probe_postgres(env) is True


def test_should_probe_postgres_false_with_explicit_sqlite_backend() -> None:
    """Explicit sqlite backend selection should suppress PostgreSQL probing."""

    env = {"ARTHEXIS_DB_BACKEND": "sqlite", "POSTGRES_HOST": "db.internal"}

    assert should_probe_postgres(env) is False


def test_should_probe_postgres_true_with_postgres_env_vars() -> None:
    """Any explicit PostgreSQL connection variable should enable probing."""

    env = {"POSTGRES_HOST": "localhost"}

    assert should_probe_postgres(env) is True
