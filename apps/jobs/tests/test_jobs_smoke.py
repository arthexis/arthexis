"""Starter smoke tests for generated app modules."""

from importlib import import_module


def test_jobs_imports() -> None:
    """Generated app modules should be importable."""

    assert import_module("apps.jobs.admin")
    assert import_module("apps.jobs.apps")
    assert import_module("apps.jobs.forms")
    assert import_module("apps.jobs.manifest")
    assert import_module("apps.jobs.models")
    assert import_module("apps.jobs.views")
    assert import_module("apps.jobs.urls")
