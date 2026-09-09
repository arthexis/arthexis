"""Channel layer configuration helpers.

This module centralizes channel-layer backend selection so we can emit
structured logs and consistently handle Redis URL fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelLayerDecision:
    """Describe how channel-layer settings were resolved."""

    backend: str
    redis_url: str
    redis_source: str
    fallback_reason: str
    shared_backend_required: bool


def _mask_redis_url(value: str) -> str:
    """Mask password components in Redis URLs before logging."""

    parsed = urlparse(value)
    if not parsed.password:
        return value
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    user = parsed.username or ""
    userinfo = f"{user}:****" if user else ":****"
    return parsed._replace(netloc=f"{userinfo}@{host}{port}").geturl()


def _validate_redis_url(value: str) -> tuple[bool, str]:
    """Return whether a Redis URL is well formed enough for Channels."""

    parsed = urlparse(value)
    if parsed.scheme not in {"redis", "rediss", "unix"}:
        return False, "unsupported_scheme"
    if parsed.scheme in {"redis", "rediss"} and not parsed.hostname:
        return False, "missing_hostname"
    if parsed.scheme == "unix" and not parsed.path:
        return False, "missing_socket_path"
    return True, ""


def resolve_channel_layers(
    *,
    channel_redis_url: str,
    ocpp_state_redis_url: str,
    shared_backend_required: bool = False,
) -> tuple[dict[str, dict[str, object]], ChannelLayerDecision]:
    """Resolve channel-layer backend configuration and emit structured logs."""

    candidates = [
        ("CHANNEL_REDIS_URL", channel_redis_url.strip()),
        ("OCPP_STATE_REDIS_URL", ocpp_state_redis_url.strip()),
    ]
    selected_source = ""
    selected_url = ""
    fallback_reason = ""
    for source, url in candidates:
        if not url:
            continue
        valid, reason = _validate_redis_url(url)
        if valid:
            selected_source = source
            selected_url = url
            break
        fallback_reason = f"{source}:{reason}"
        logger.warning(
            "channel_layer.redis_url_invalid",
            extra={
                "event": "channel_layer.redis_url_invalid",
                "source": source,
                "value": _mask_redis_url(url),
                "reason": reason,
            },
        )

    if selected_url:
        decision = ChannelLayerDecision(
            backend="channels_redis.core.RedisChannelLayer",
            redis_url=selected_url,
            redis_source=selected_source,
            fallback_reason=fallback_reason,
            shared_backend_required=shared_backend_required,
        )
        logger.info(
            "channel_layer.initialized",
            extra={
                "event": "channel_layer.initialized",
                "backend": decision.backend,
                "redis_source": decision.redis_source,
                "redis_url": _mask_redis_url(decision.redis_url),
                "fallback_reason": decision.fallback_reason,
                "shared_backend_required": decision.shared_backend_required,
            },
        )
        return (
            {
                "default": {
                    "BACKEND": decision.backend,
                    "CONFIG": {"hosts": [decision.redis_url]},
                }
            },
            decision,
        )

    decision = ChannelLayerDecision(
        backend="channels.layers.InMemoryChannelLayer",
        redis_url="",
        redis_source="",
        fallback_reason=fallback_reason or "missing_redis_url",
        shared_backend_required=shared_backend_required,
    )
    log = logger.warning if shared_backend_required else logger.info
    log(
        "channel_layer.fallback_inmemory",
        extra={
            "event": "channel_layer.fallback_inmemory",
            "backend": decision.backend,
            "fallback_reason": decision.fallback_reason,
            "shared_backend_required": decision.shared_backend_required,
        },
    )
    return ({"default": {"BACKEND": decision.backend}}, decision)
