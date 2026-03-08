"""Channels and OCPP-related settings."""

import os

from config.channel_layer import resolve_channel_layers

from utils.env import env_bool

from .broker import resolve_celery_broker_url

# Channels configuration
CHANNEL_REDIS_URL = os.environ.get("CHANNEL_REDIS_URL", "").strip()
OCPP_STATE_REDIS_URL = os.environ.get("OCPP_STATE_REDIS_URL", "").strip()
if not OCPP_STATE_REDIS_URL:
    OCPP_STATE_REDIS_URL = (
        CHANNEL_REDIS_URL
        or resolve_celery_broker_url()
    )

CHANNEL_LAYERS, CHANNEL_LAYER_DECISION = resolve_channel_layers(
    channel_redis_url=CHANNEL_REDIS_URL,
    ocpp_state_redis_url=OCPP_STATE_REDIS_URL,
)

OCPP_PENDING_CALL_TTL = int(os.environ.get("OCPP_PENDING_CALL_TTL", "1800"))
OCPP_ASYNC_LOGGING = env_bool(
    "OCPP_ASYNC_LOGGING", bool(CHANNEL_REDIS_URL or OCPP_STATE_REDIS_URL)
)
try:
    OCPP_FORWARDER_PING_INTERVAL = int(os.environ.get("OCPP_FORWARDER_PING_INTERVAL", "60"))
except (TypeError, ValueError):
    OCPP_FORWARDER_PING_INTERVAL = 60
if OCPP_FORWARDER_PING_INTERVAL <= 0:
    OCPP_FORWARDER_PING_INTERVAL = 60
