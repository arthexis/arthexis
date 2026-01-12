from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.screens.lcd import LCDUnavailableError
from apps.screens.models import LCDAnimation


@pytest.mark.django_db
def test_lists_available_animations(capsys):
    LCDAnimation.objects.create(
        slug="trees", name="Trees", source_path="/tmp/trees.txt", is_active=True
    )
    LCDAnimation.objects.create(
        slug="stars", name="Stars", source_path="/tmp/stars.txt", is_active=False
    )

    call_command("lcd_animate")

    out = capsys.readouterr().out
    assert "Available animations:" in out
    assert "trees: Trees" in out
    assert "stars: Stars [inactive]" in out


@pytest.mark.django_db
def test_reports_missing_animation():
    with pytest.raises(CommandError, match="LCD animation 'missing' not found"):
        call_command("lcd_animate", "missing")


@pytest.mark.django_db
def test_plays_animation_to_work_file(monkeypatch, tmp_path: Path):
    animation_file = tmp_path / "frames.txt"
    animation_file.write_text("A" * 32 + "\n" + "B" * 32 + "\n", encoding="utf-8")

    LCDAnimation.objects.create(
        slug="demo",
        name="Demo",
        source_path=str(animation_file),
        frame_interval_ms=0,
    )

    def _fail_init(*args, **kwargs):
        raise LCDUnavailableError("no lcd")

    monkeypatch.setattr(
        "apps.screens.management.commands.lcd_animate.prepare_lcd_controller",
        _fail_init,
    )

    with override_settings(BASE_DIR=tmp_path):
        call_command("lcd_animate", "demo", loops=1, interval=0)

    output_file = tmp_path / "work" / "lcd-animate.txt"
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8").splitlines() == ["B" * 16, "B" * 16]
