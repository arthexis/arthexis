from apps.core.system import lifecycle


def test_terminal_does_not_require_local_redis(capsys) -> None:
    lifecycle._warn_if_local_redis_missing("Terminal")

    assert capsys.readouterr().err == ""


def test_available_local_redis_emits_no_guidance(monkeypatch, capsys) -> None:
    monkeypatch.setattr(lifecycle, "_redis_is_available", lambda host, port: True)

    lifecycle._warn_if_local_redis_missing("Control")

    assert capsys.readouterr().err == ""


def test_missing_local_redis_emits_debian_guidance(monkeypatch, capsys) -> None:
    monkeypatch.setattr(lifecycle, "_redis_is_available", lambda host, port: False)
    monkeypatch.setattr(lifecycle, "_os_id", lambda: "debian")

    lifecycle._warn_if_local_redis_missing("Watchtower")

    error = capsys.readouterr().err
    assert "Watchtower requires Redis for Celery/Channels" in error
    assert "sudo apt-get install -y redis-server" in error
    assert "sudo systemctl enable --now redis-server" in error
    assert "redis-cli ping" in error


def test_remote_redis_does_not_trigger_local_install_guidance(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis.example:6379/0")

    lifecycle._warn_if_local_redis_missing("Satellite")

    assert capsys.readouterr().err == ""
