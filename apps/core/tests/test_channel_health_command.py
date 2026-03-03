"""Tests for channel health management command output."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from apps.core.management.commands import channel_health as command


@pytest.mark.django_db
def test_channel_health_reports_inmemory_backend(monkeypatch, settings) -> None:
    """The command should emit a JSON payload for in-memory channel layers."""

    monkeypatch.setattr(
        settings,
        "CHANNEL_LAYER_DECISION",
        SimpleNamespace(
            backend="channels.layers.InMemoryChannelLayer",
            redis_source="",
            redis_url="",
            fallback_reason="missing_redis_url",
        ),
    )

    stdout = io.StringIO()
    call_command("channel_health", stdout=stdout)
    output = stdout.getvalue()

    assert '"backend": "channels.layers.InMemoryChannelLayer"' in output
    assert '"redis_ping": null' in output


@pytest.mark.django_db
def test_channel_health_reports_redis_error(monkeypatch, settings) -> None:
    """Redis ping failures should be included in the health payload."""

    monkeypatch.setattr(
        settings,
        "CHANNEL_LAYER_DECISION",
        SimpleNamespace(
            backend="channels_redis.core.RedisChannelLayer",
            redis_source="CHANNEL_REDIS_URL",
            redis_url="redis://localhost:6379/0",
            fallback_reason="",
        ),
    )

    class FakeClient:
        """Simple fake Redis client that raises on ping."""

        def ping(self):
            raise ValueError("bad redis")

    monkeypatch.setattr(command.Redis, "from_url", lambda *_args, **_kwargs: FakeClient())

    stdout = io.StringIO()
    call_command("channel_health", stdout=stdout)
    output = stdout.getvalue()

    assert '"redis_ping": false' in output
    assert '"redis_error": "bad redis"' in output
