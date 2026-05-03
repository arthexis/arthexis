from datetime import datetime, timezone

from apps.summary import services
from apps.tasks.tasks import _write_lcd_frames


def test_fixed_frame_window_does_not_pad_blank_frames() -> None:
    frames = services.fixed_frame_window([("A", "B"), ("C", "D")])

    assert frames == [("A", "B"), ("C", "D")]


def test_summary_frames_are_written_with_expiry(tmp_path) -> None:
    expires_at = datetime(2026, 5, 3, 14, 30, tzinfo=timezone.utc)

    _write_lcd_frames(
        [("OK", "No errors")],
        lock_file=tmp_path / "lcd-summary",
        expires_at=expires_at,
    )

    assert (tmp_path / "lcd-summary").read_text(encoding="utf-8").splitlines() == [
        "OK",
        "No errors",
        "2026-05-03T14:30:00+00:00",
    ]


def test_legacy_low_summary_frames_are_removed(tmp_path) -> None:
    (tmp_path / "lcd-low").write_text("old\nsummary\n", encoding="utf-8")
    (tmp_path / "lcd-low-1").write_text("old\nsummary\n", encoding="utf-8")
    (tmp_path / "lcd-low-2").write_text("old\nsummary\n", encoding="utf-8")
    (tmp_path / "lcd-low-extra").write_text("keep\nme\n", encoding="utf-8")

    services.clear_legacy_low_summary_frames(tmp_path)

    assert not (tmp_path / "lcd-low").exists()
    assert not (tmp_path / "lcd-low-1").exists()
    assert not (tmp_path / "lcd-low-2").exists()
    assert (tmp_path / "lcd-low-extra").exists()
