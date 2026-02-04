from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from apps.core.management.commands import redis as redis_command


@pytest.mark.django_db
def test_redis_command_shows_status_and_config(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "CHANNEL_REDIS_URL", "redis://example.test/0")
    monkeypatch.setattr(settings, "OCPP_STATE_REDIS_URL", "")
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://example.test/1")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "cache+memory://")
    monkeypatch.setattr(settings, "VIDEO_FRAME_REDIS_URL", "")
    (tmp_path / "redis.env").write_text(
        "CELERY_BROKER_URL=redis://localhost:6379/0\n", encoding="utf-8"
    )

    def fake_run(args, capture_output=False, text=False):
        status = "active" if args[-1] == "redis-server" else "inactive"
        return SimpleNamespace(returncode=0, stdout=f"{status}\n")

    monkeypatch.setattr(redis_command.subprocess, "run", fake_run)
    monkeypatch.setattr(redis_command.shutil, "which", lambda _: "/bin/systemctl")

    fake_client = SimpleNamespace(ping=lambda: True)
    monkeypatch.setattr(redis_command.Redis, "from_url", lambda *_args, **_kwargs: fake_client)

    stdout = io.StringIO()
    call_command("redis", stdout=stdout)
    output = stdout.getvalue()

    assert "redis-server: active" in output
    assert "CHANNEL_REDIS_URL: redis://example.test/0" in output
    assert "CELERY_BROKER_URL: redis://example.test/1" in output
    assert "redis.env:" in output
    assert "CELERY_BROKER_URL=redis://localhost:6379/0" in output
    assert "Redis connectivity: OK" in output


@pytest.mark.django_db
def test_redis_command_report_includes_memory(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "CHANNEL_REDIS_URL", "redis://example.test/0")
    monkeypatch.setattr(settings, "OCPP_STATE_REDIS_URL", "")
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://example.test/1")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "cache+memory://")
    monkeypatch.setattr(settings, "VIDEO_FRAME_REDIS_URL", "")

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="inactive\n")

    monkeypatch.setattr(redis_command.subprocess, "run", fake_run)
    monkeypatch.setattr(redis_command.shutil, "which", lambda _: "/bin/systemctl")

    def fake_info(section=None):
        if section == "memory":
            return {
                "used_memory_human": "12.3M",
                "used_memory_peak_human": "20M",
                "used_memory_rss_human": "30M",
                "maxmemory_human": "0B",
                "maxmemory_policy": "noeviction",
            }
        if section == "server":
            return {"redis_version": "7.2.0", "uptime_in_days": 3}
        if section == "keyspace":
            return {"db0": "keys=1,expires=0,avg_ttl=0"}
        return {}

    fake_client = SimpleNamespace(ping=lambda: True, info=fake_info)
    monkeypatch.setattr(redis_command.Redis, "from_url", lambda *_args, **_kwargs: fake_client)

    stdout = io.StringIO()
    call_command("redis", "--report", stdout=stdout)
    output = stdout.getvalue()

    assert "Redis report:" in output
    assert "Memory usage:" in output
    assert "Used: 12.3M" in output
    assert "Eviction policy: noeviction" in output
