"""Tests for channel layer backend fallback behavior."""

from __future__ import annotations

from config.channel_layer import resolve_channel_layers


def test_channel_layers_fallback_to_inmemory_when_urls_missing() -> None:
    """Missing Redis URLs should select the in-memory channel layer backend."""

    channel_layers, decision = resolve_channel_layers(
        channel_redis_url="",
        ocpp_state_redis_url="",
    )

    assert channel_layers["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"
    assert decision.backend == "channels.layers.InMemoryChannelLayer"
    assert decision.fallback_reason == "missing_redis_url"


def test_channel_layers_fallback_when_primary_redis_url_is_malformed() -> None:
    """Malformed Redis URLs should trigger in-memory fallback."""

    channel_layers, decision = resolve_channel_layers(
        channel_redis_url="http://invalid-host:6379/0",
        ocpp_state_redis_url="",
    )

    assert channel_layers["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"
    assert decision.backend == "channels.layers.InMemoryChannelLayer"
    assert decision.fallback_reason == "CHANNEL_REDIS_URL:unsupported_scheme"


def test_channel_layers_uses_ocpp_state_url_when_primary_missing() -> None:
    """Secondary OCPP Redis URL should be used when channel URL is empty."""

    channel_layers, decision = resolve_channel_layers(
        channel_redis_url="",
        ocpp_state_redis_url="redis://localhost:6379/9",
    )

    assert channel_layers["default"]["BACKEND"] == "channels_redis.core.RedisChannelLayer"
    assert channel_layers["default"]["CONFIG"]["hosts"] == ["redis://localhost:6379/9"]
    assert decision.redis_source == "OCPP_STATE_REDIS_URL"
