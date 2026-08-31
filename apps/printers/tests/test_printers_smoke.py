"""Starter smoke tests for generated app modules."""

from importlib import import_module


def test_printers_imports() -> None:
    """Generated app modules should be importable."""

    assert import_module("apps.printers.apps")
    assert import_module("apps.printers.manifest")
    assert import_module("apps.printers.models")
