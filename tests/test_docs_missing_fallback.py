"""Regression tests for documentation fallback rendering."""

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from apps.docs import views


pytestmark = pytest.mark.critical


def test_missing_docs_path_renders_library_fallback(monkeypatch):
    """Regression: missing docs routes should show the library fallback instead of a blank page."""

    request = RequestFactory().get("/docs/does-not-exist")

    def raise_missing(*_args, **_kwargs):
        raise Http404("Document not found")

    monkeypatch.setattr(views, "render_readme_page", raise_missing)
    monkeypatch.setattr(views, "_collect_document_library", lambda *_args, **_kwargs: [])

    captured = {}

    def fake_render(_request, _template, context, status=200):
        captured["context"] = context
        return HttpResponse("fallback", status=status)

    monkeypatch.setattr(views, "render", fake_render)

    response = views.readme(request, doc="does-not-exist", prepend_docs=True)

    assert response.status_code == 404
    assert captured["context"]["missing_document"] == "docs/does-not-exist"
