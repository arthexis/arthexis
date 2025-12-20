import types

import pytest
from django.db.utils import OperationalError
from django.test import RequestFactory

from apps.sites import context_processors


@pytest.mark.django_db
def test_nav_links_handles_missing_modules_table(monkeypatch):
    request = RequestFactory().get("/admin/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))

    class BrokenQuerySet:
        def filter(self, **kwargs):
            return self

        def select_related(self, *args, **kwargs):
            return self

        def prefetch_related(self, *args, **kwargs):
            return self

        def __iter__(self):
            raise OperationalError("modules table missing")

    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        types.MethodType(lambda self, role: BrokenQuerySet(), context_processors.Module.objects),
    )

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []
