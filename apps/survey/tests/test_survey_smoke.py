"""Starter smoke tests for generated app modules."""

from importlib import import_module


def test_survey_imports() -> None:
    """Generated app modules should be importable."""

    assert import_module("apps.survey.apps")
    assert import_module("apps.survey.manifest")
    assert import_module("apps.survey.models")
    assert import_module("apps.survey.views")
    assert import_module("apps.survey.urls")
