import json
from datetime import datetime, timedelta, timezone

from apps.screens.history import (
    LCDHistoryRecorder,
    load_history_entries,
    select_entry_for_timestamp,
)


def test_history_recorder_rotates_between_days(tmp_path):
    current = datetime.now(timezone.utc)

    def clock():
        return current

    recorder = LCDHistoryRecorder(base_dir=tmp_path, clock=clock)
    recorder.record("line1", "line2", label="first")

    history_dir = tmp_path / "work"
    first_entry = json.loads((history_dir / "lcd-history-0.txt").read_text())
    assert first_entry["line1"] == "line1"
    assert first_entry["label"] == "first"

    current = current + timedelta(days=1)
    recorder.record("next", "day")

    day_zero = (history_dir / "lcd-history-0.txt").read_text().splitlines()
    day_one = (history_dir / "lcd-history-1.txt").read_text().splitlines()

    assert json.loads(day_zero[0])["line1"] == "next"
    assert json.loads(day_one[0])["line1"] == "line1"


def test_select_entry_for_timestamp_picks_latest_entry(tmp_path):
    now = datetime.now(timezone.utc)
    recorder = LCDHistoryRecorder(base_dir=tmp_path, clock=lambda: now)
    recorder.record("a", "b")
    later = now + timedelta(minutes=5)
    recorder.clock = lambda: later
    recorder.record("c", "d")

    entries = load_history_entries(tmp_path)
    chosen = select_entry_for_timestamp(entries, now + timedelta(minutes=2))

    assert chosen is not None
    assert chosen.line1 == "a"
    assert chosen.line2 == "b"
