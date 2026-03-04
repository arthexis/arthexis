"""Smoke tests for assembled Django settings imports."""

import importlib


def test_settings_import_smoke_defines_critical_settings() -> None:
    """Importing the settings package should expose key configuration attributes."""

    settings_module = importlib.import_module("config.settings")

    assert hasattr(settings_module, "INSTALLED_APPS")
    assert hasattr(settings_module, "CHANNEL_LAYERS")
    assert hasattr(settings_module, "AUTHENTICATION_BACKENDS")
