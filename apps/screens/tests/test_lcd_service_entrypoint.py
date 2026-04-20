from __future__ import annotations

import os
from unittest import mock

from apps.screens.lcd_screen import __main__ as lcd_service_main


def test_main_bootstraps_django_before_running_service(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)

    with (
        mock.patch("django.setup") as mock_setup,
        mock.patch.object(lcd_service_main, "_run_lcd_service") as mock_run,
    ):
        lcd_service_main.main()

    assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings"
    mock_setup.assert_called_once_with()
    mock_run.assert_called_once_with()


def test_main_preserves_existing_django_settings_module(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "custom.settings")

    with (
        mock.patch("django.setup") as mock_setup,
        mock.patch.object(lcd_service_main, "_run_lcd_service") as mock_run,
    ):
        lcd_service_main.main()

    assert os.environ["DJANGO_SETTINGS_MODULE"] == "custom.settings"
    mock_setup.assert_called_once_with()
    mock_run.assert_called_once_with()
