"""Redis-backed helpers for OCPP store state."""

from __future__ import annotations

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

from .state import MAX_CONNECTIONS_PER_IP, ip_connections

_STATE_REDIS: Redis | None = None
_STATE_REDIS_URL = getattr(settings, "OCPP_STATE_REDIS_URL", "")
_PENDING_TTL = int(getattr(settings, "OCPP_PENDING_CALL_TTL", 1800) or 1800)
_IP_CONNECTION_TTL = 3600


def _state_redis() -> Redis | None:
    global _STATE_REDIS
    if not _STATE_REDIS_URL:
        return None
    if _STATE_REDIS is None:
        try:
            _STATE_REDIS = Redis.from_url(_STATE_REDIS_URL, decode_responses=True)
        except Exception:  # pragma: no cover - best effort fallback
            _STATE_REDIS = None
    return _STATE_REDIS


def _connection_token(consumer: object) -> str:
    token = getattr(consumer, "_ocpp_state_token", None)
    if token:
        return token
    token = getattr(consumer, "channel_name", None) or f"consumer-{id(consumer)}"
    try:
        setattr(consumer, "_ocpp_state_token", token)
    except Exception:  # pragma: no cover - best effort
        pass
    return token


def _redis_ip_key(ip: str) -> str:
    return f"ocpp:ip-connection:{ip}"


def _register_ip_connection_redis(ip: str, consumer: object) -> bool | None:
    client = _state_redis()
    if not client:
        return None
    key = _redis_ip_key(ip)
    token = _connection_token(consumer)
    try:
        pipe = client.pipeline()
        pipe.sadd(key, token)
        pipe.expire(key, _IP_CONNECTION_TTL)
        pipe.scard(key)
        added, _expired, count = pipe.execute()
        if count > MAX_CONNECTIONS_PER_IP and added:
            client.srem(key, token)
            return False
        return count <= MAX_CONNECTIONS_PER_IP
    except RedisError:
        return None


def _release_ip_connection_redis(ip: str, consumer: object) -> None:
    client = _state_redis()
    if not client:
        return
    key = _redis_ip_key(ip)
    token = _connection_token(consumer)
    try:
        client.srem(key, token)
    except RedisError:
        return


def register_ip_connection(ip: str | None, consumer: object) -> bool:
    """Track a websocket connection for the provided client IP."""

    if not ip:
        return True
    allowed = _register_ip_connection_redis(ip, consumer)
    if allowed is False:
        return False
    conns = ip_connections.setdefault(ip, set())
    if consumer in conns:
        return True
    if len(conns) >= MAX_CONNECTIONS_PER_IP:
        if allowed:
            _release_ip_connection_redis(ip, consumer)
        return False
    conns.add(consumer)
    return True


def release_ip_connection(ip: str | None, consumer: object) -> None:
    """Remove a websocket connection from the active client registry."""

    if not ip:
        return
    _release_ip_connection_redis(ip, consumer)
    conns = ip_connections.get(ip)
    if not conns:
        return
    conns.discard(consumer)
    if not conns:
        ip_connections.pop(ip, None)


__all__ = [
    "_STATE_REDIS",
    "_STATE_REDIS_URL",
    "_PENDING_TTL",
    "_IP_CONNECTION_TTL",
    "_state_redis",
    "_connection_token",
    "_redis_ip_key",
    "_register_ip_connection_redis",
    "_release_ip_connection_redis",
    "register_ip_connection",
    "release_ip_connection",
]
