from __future__ import annotations

from io import StringIO

from django.core.management import call_command


def test_startup_message_command_prints_task_status(monkeypatch):
    captured: dict[str, str | None] = {}

    def fake_send_startup_net_message(*, lock_file=None, port=None):
        captured["lock_file"] = lock_file
        captured["port"] = port
        return "queued:/tmp/lcd-high"

    monkeypatch.setattr(
        "apps.nodes.management.commands.startup_message.send_startup_net_message",
        fake_send_startup_net_message,
    )

    stdout = StringIO()
    call_command(
        "startup_message",
        "--port",
        "9999",
        "--lock-file",
        "/tmp/lcd-high",
        stdout=stdout,
    )

    assert stdout.getvalue().strip() == "queued:/tmp/lcd-high"
    assert captured == {"lock_file": "/tmp/lcd-high", "port": "9999"}
