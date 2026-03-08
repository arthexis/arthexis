"""Tests for shared broker URL resolution helpers."""

from __future__ import annotations

from config.settings.broker import resolve_celery_broker_url


def test_resolve_celery_broker_url_prefers_legacy_broker_env(monkeypatch) -> None:
    """Legacy BROKER_URL should still drive broker resolution when present."""

    monkeypatch.setenv("BROKER_URL", "redis://localhost:6379/9")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    assert resolve_celery_broker_url(node_role="watchtower") == "redis://localhost:6379/9"


def test_resolve_celery_broker_url_uses_role_defaults(monkeypatch) -> None:
    """Default broker fallback remains role-aware without explicit env URLs."""

    monkeypatch.delenv("BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    assert resolve_celery_broker_url(node_role="watchtower") == "redis://localhost:6379/0"
    assert resolve_celery_broker_url(node_role="terminal") == "memory://localhost/"
