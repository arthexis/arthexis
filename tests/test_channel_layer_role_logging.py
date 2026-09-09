import logging

from config.channel_layer import resolve_channel_layers


def test_optional_inmemory_fallback_is_informational(caplog):
    with caplog.at_level(logging.INFO, logger="config.channel_layer"):
        _, decision = resolve_channel_layers(
            channel_redis_url="",
            ocpp_state_redis_url="",
            shared_backend_required=False,
        )

    assert decision.backend == "channels.layers.InMemoryChannelLayer"
    assert decision.shared_backend_required is False
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "channel_layer.fallback_inmemory"
    )
    assert record.levelno == logging.INFO


def test_required_inmemory_fallback_remains_warning(caplog):
    with caplog.at_level(logging.INFO, logger="config.channel_layer"):
        _, decision = resolve_channel_layers(
            channel_redis_url="",
            ocpp_state_redis_url="",
            shared_backend_required=True,
        )

    assert decision.shared_backend_required is True
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "channel_layer.fallback_inmemory"
    )
    assert record.levelno == logging.WARNING
