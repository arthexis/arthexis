"""Starter smoke tests for generated app modules."""

from importlib import import_module


def test_skills_imports() -> None:
    """Generated app modules should be importable."""

    assert import_module("apps.skills.apps")
    assert import_module("apps.skills.manifest")
    assert import_module("apps.skills.models")
