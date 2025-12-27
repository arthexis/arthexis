from __future__ import annotations

import json

import pytest

from apps.screens import lcd_screen


def test_timing_anchor_alignment_delay():
    anchor = lcd_screen.TimingAnchor(boot_id="boot", offset=0.25)

    delay = anchor.alignment_delay(now=10.10)
    assert pytest.approx(0.15, rel=1e-3) == delay

    zero_delay = anchor.alignment_delay(now=10.25)
    assert zero_delay == 0.0


def test_timing_anchor_persist_and_reload(tmp_path):
    path = tmp_path / "lcd-timing.json"
    anchor = lcd_screen.TimingAnchor(boot_id="abc", offset=0.6)

    anchor.persist(path)

    loaded = lcd_screen.TimingAnchor.load(path, expected_boot="abc")
    assert loaded == anchor

    stale = lcd_screen.TimingAnchor.load(path, expected_boot="other")
    assert stale is None
    assert not path.exists()


def test_capture_timing_anchor_records_once(tmp_path):
    path = tmp_path / "lcd-timing.json"
    anchor = lcd_screen._capture_timing_anchor(
        path=path,
        boot_id="boot",
        existing=None,
        clock_source=lambda: 12.75,
    )

    assert anchor
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "boot_id": "boot",
        "offset": pytest.approx(0.75, rel=1e-6),
    }

    # Existing anchors are reused even if a different clock value is provided.
    reused = lcd_screen._capture_timing_anchor(
        path=path,
        boot_id="boot",
        existing=anchor,
        clock_source=lambda: 0.1,
    )
    assert reused == anchor
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "boot_id": "boot",
        "offset": pytest.approx(0.75, rel=1e-6),
    }
