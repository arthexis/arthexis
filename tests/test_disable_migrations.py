import os

import pytest

from conftest import _DisableMigrations, _configure_migrations


def test_disable_migrations_allowlists_selected_apps():
    mapping = _DisableMigrations(enabled_apps={"ocpp"})

    assert "ocpp" not in mapping
    assert mapping["other"] is None

    with pytest.raises(KeyError):
        _ = mapping["ocpp"]


def test_pytest_disable_migrations_is_overridden(monkeypatch, settings):
    monkeypatch.setenv("PYTEST_DISABLE_MIGRATIONS", "1")
    settings.MIGRATION_MODULES = {"sample": None}

    _configure_migrations()

    assert os.environ.get("PYTEST_DISABLE_MIGRATIONS") == "0"
    assert settings.MIGRATION_MODULES == {"sample": None}
