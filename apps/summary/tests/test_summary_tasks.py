from pathlib import Path

from apps.tasks.tasks import _write_lcd_frames
from apps.summary.services import render_lcd_payload


def test_write_lcd_frames_updates_lock_file(tmp_path):
    lock_file = tmp_path / "lcd-low"
    frames = [("HELLO", "WORLD"), ("LAST", "FRAME")]

    _write_lcd_frames(frames, lock_file=lock_file)

    assert lock_file.exists()
    expected_first = render_lcd_payload("HELLO", "WORLD")
    expected_second = render_lcd_payload("LAST", "FRAME")
    assert lock_file.read_text(encoding="utf-8") == expected_first
    assert (tmp_path / "lcd-low-1").read_text(encoding="utf-8") == expected_second


def test_write_lcd_frames_removes_stale_channel_files(tmp_path):
    lock_file = tmp_path / "lcd-low"
    (tmp_path / "lcd-low-0").write_text("old\n", encoding="utf-8")
    (tmp_path / "lcd-low-1").write_text("old\n", encoding="utf-8")
    (tmp_path / "lcd-low-2").write_text("old\n", encoding="utf-8")

    _write_lcd_frames([("ONLY", "ONE")], lock_file=lock_file)

    assert lock_file.exists()
    assert not (tmp_path / "lcd-low-0").exists()
    assert not (tmp_path / "lcd-low-1").exists()
    assert not (tmp_path / "lcd-low-2").exists()


def test_write_lcd_frames_removes_all_files_when_no_frames(tmp_path):
    lock_file = tmp_path / "lcd-low"
    lock_file.write_text("old\n", encoding="utf-8")
    (tmp_path / "lcd-low-0").write_text("old\n", encoding="utf-8")
    (tmp_path / "lcd-low-1").write_text("old\n", encoding="utf-8")

    _write_lcd_frames([], lock_file=lock_file)

    assert not lock_file.exists()
    assert not (tmp_path / "lcd-low-0").exists()
    assert not (tmp_path / "lcd-low-1").exists()
