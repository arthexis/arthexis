from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from apps.core.management.commands import redis as redis_command


@pytest.mark.django_db
def test_redis_command_redacts_configured_urls_and_redis_env_secrets(
    monkeypatch, settings, tmp_path
):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        settings, "CHANNEL_REDIS_URL", "redis://user:secret@example.test/0"
    )
    monkeypatch.setattr(settings, "OCPP_STATE_REDIS_URL", "")
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://:s3cr3t@example.test/1")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "cache+memory://")
    monkeypatch.setattr(settings, "VIDEO_FRAME_REDIS_URL", "")
    (tmp_path / "redis.env").write_text(
        "CELERY_BROKER_URL=redis://:p4ss@localhost:6379/0\nREDIS_PASSWORD=plainsecret\n",
        encoding="utf-8",
    )

    def fake_run(args, capture_output=False, text=False):
        status = "active" if args[-1] == "redis-server" else "inactive"
        return SimpleNamespace(returncode=0, stdout=f"{status}\n")

    monkeypatch.setattr(redis_command.subprocess, "run", fake_run)
    monkeypatch.setattr(redis_command.shutil, "which", lambda _: "/bin/systemctl")

    fake_client = SimpleNamespace(ping=lambda: True)
    monkeypatch.setattr(
        redis_command.Redis, "from_url", lambda *_args, **_kwargs: fake_client
    )

    stdout = io.StringIO()
    call_command("redis", stdout=stdout)
    output = stdout.getvalue()

    assert "CHANNEL_REDIS_URL: redis://user:****@example.test/0" in output
    assert "CELERY_BROKER_URL: redis://:****@example.test/1" in output
    assert "CELERY_BROKER_URL=redis://:****@localhost:6379/0" in output
    assert "REDIS_PASSWORD=****" in output
    assert "REDIS_PASSWORD=plainsecret" not in output
