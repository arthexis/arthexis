from config.settings.broker import resolve_redis_broker_fallback


def test_terminal_memory_broker_is_not_reused_as_redis(monkeypatch):
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("BROKER_URL", raising=False)

    assert resolve_redis_broker_fallback(node_role="Terminal") == ""


def test_shared_role_default_redis_broker_is_reused(monkeypatch):
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("BROKER_URL", raising=False)

    assert resolve_redis_broker_fallback(node_role="Control") == "redis://localhost:6379/0"


def test_explicit_memory_broker_is_not_reused_as_redis(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "memory://localhost/")

    assert resolve_redis_broker_fallback(node_role="Control") == ""


def test_explicit_redis_broker_is_reused(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "rediss://redis.example:6380/2")

    assert (
        resolve_redis_broker_fallback(node_role="Terminal")
        == "rediss://redis.example:6380/2"
    )
