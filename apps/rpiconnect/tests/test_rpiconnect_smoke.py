"""Starter smoke tests for generated app modules."""

from importlib import import_module


def test_rpiconnect_imports() -> None:
    """Generated app modules should be importable."""

    assert import_module("apps.rpiconnect.apps")
    assert import_module("apps.rpiconnect.manifest")
    assert import_module("apps.rpiconnect.models")
    assert import_module("apps.rpiconnect.views")
    assert import_module("apps.rpiconnect.urls")
