from __future__ import annotations

from datetime import datetime, timezone

from django.core.management import call_command
from django.test import override_settings

from apps.screens.history import LCDHistoryRecorder
from apps.screens.management.commands import lcd_replay


class StubWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def write(self, line1: str, line2: str, **_: object) -> bool:
        self.calls.append((line1, line2))
        return True


class StubReplayer:
    def __init__(self, writer: StubWriter) -> None:
        self.ran = False
        self.writer = writer

    def run(self) -> None:
        self.ran = True
        self.writer.write("stub", "frame")


def test_lcd_replay_command_uses_history(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    recorder = LCDHistoryRecorder(base_dir=tmp_path, clock=lambda: now)
    recorder.record("hello", "world")

    writer = StubWriter()
    replayer = StubReplayer(writer)

    monkeypatch.setattr(
        lcd_replay.Command, "_build_writer", lambda self, base_dir: writer
    )
    monkeypatch.setattr(
        lcd_replay.Command,
        "_interrupt_lcd_service",
        lambda self, service_name: True,
    )
    monkeypatch.setattr(
        lcd_replay.Command,
        "_build_replayer",
        lambda self, entries, built_writer, start_index: replayer,
    )

    with override_settings(BASE_DIR=tmp_path):
        call_command("lcd_replay", minutes=1)

    assert replayer.ran is True
    assert writer.calls
