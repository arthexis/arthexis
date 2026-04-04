from __future__ import annotations

import importlib
import sys
import types

from apps.nodes.apps import NodesConfig


def _nodes_config() -> NodesConfig:
    return NodesConfig("apps.nodes", importlib.import_module("apps.nodes"))


def test_should_enqueue_startup_message_for_runserver_and_asgi_entrypoints(monkeypatch):
    config = _nodes_config()

    monkeypatch.setattr("apps.nodes.apps.sys.argv", ["daphne", "config.asgi:application"])
    assert config._should_enqueue_startup_message() is True

    monkeypatch.setattr("apps.nodes.apps.sys.argv", ["gunicorn", "config.asgi:application"])
    assert config._should_enqueue_startup_message() is True

    monkeypatch.setattr("apps.nodes.apps.sys.argv", ["python", "manage.py", "runserver"])
    assert config._should_enqueue_startup_message() is True


def test_ready_sends_startup_message_without_celery_enqueue(monkeypatch):
    config = _nodes_config()
    captured: dict[str, str | None] = {}

    monkeypatch.setitem(
        sys.modules,
        "apps.nodes.signals",
        types.ModuleType("apps.nodes.signals"),
    )
    monkeypatch.setattr("apps.nodes.apps.sys.argv", ["manage.py", "runserver"])
    monkeypatch.setenv("RUN_MAIN", "true")
    monkeypatch.setenv("PORT", "9999")

    def _fake_send_startup_net_message(*, port=None):
        captured["port"] = port
        return "queued"

    monkeypatch.setattr(
        "apps.nodes.tasks.send_startup_net_message",
        _fake_send_startup_net_message,
    )

    config.ready()

    assert captured == {"port": "9999"}
