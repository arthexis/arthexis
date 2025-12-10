import pathlib

import pytest
from django.test import override_settings

from apps.docs import views


class _DummyQuerySet:
    def filter(self, *args, **kwargs):
        return self

    def select_related(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _DummyManager:
    def for_role(self, role):
        return _DummyQuerySet()


@pytest.mark.django_db
@override_settings()
def test_locate_readme_prefers_localized_documents(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(views.Module, "objects", _DummyManager())

    localized = tmp_path / "docs" / "guide.es.md"
    localized.parent.mkdir(parents=True)
    localized.write_text("spanish")

    fallback = tmp_path / "docs" / "guide.md"
    fallback.write_text("english")

    settings.BASE_DIR = tmp_path

    document = views._locate_readme_document(role=None, doc="docs/guide", lang="es")

    assert document.file == localized
