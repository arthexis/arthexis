"""Tests for application model signal behavior."""

from __future__ import annotations

from types import SimpleNamespace

from apps.app import signals


def test_sync_application_models_runs_only_for_final_post_migrate_app(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        signals,
        "refresh_application_models",
        lambda *, using: calls.append(using),
    )
    monkeypatch.setattr(signals, "is_final_post_migrate_app", lambda app_config: False)

    signals.sync_application_models(
        sender=SimpleNamespace(label="gallery"),
        app_config=SimpleNamespace(label="gallery"),
        using="default",
    )

    assert calls == []

    monkeypatch.setattr(signals, "is_final_post_migrate_app", lambda app_config: True)

    signals.sync_application_models(
        sender=SimpleNamespace(label="widgets"),
        app_config=SimpleNamespace(label="widgets"),
        using="default",
    )

    assert calls == ["default"]
