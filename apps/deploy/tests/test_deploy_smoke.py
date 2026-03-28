"""Starter smoke tests for generated app modules."""

from importlib import import_module


def test_deploy_imports() -> None:
    """Generated app modules should be importable."""

    assert import_module("apps.deploy.apps")
    assert import_module("apps.deploy.manifest")
    assert import_module("apps.deploy.models")
