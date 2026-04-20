"""Tests for lcd_screen package import behavior."""

from __future__ import annotations

import importlib
import sys


def test_lcd_screen_package_does_not_eager_import_runner():
    sys.modules.pop("apps.screens.lcd_screen", None)
    sys.modules.pop("apps.screens.lcd_screen.runner", None)

    package = importlib.import_module("apps.screens.lcd_screen")

    assert "apps.screens.lcd_screen.runner" not in sys.modules
    assert callable(package.main)
