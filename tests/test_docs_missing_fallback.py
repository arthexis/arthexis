"""Regression tests for documentation fallback rendering."""

from pathlib import Path

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from apps.docs import views


pytestmark = pytest.mark.critical


def test_missing_docs_path_renders_library_fallback(monkeypatch):
    """Regression: missing docs routes should show the library fallback instead of a blank page."""

    request = RequestFactory().get("/docs/does-not-exist")

    def raise_missing(*_args, **_kwargs):
        raise Http404(views.DOCUMENT_NOT_FOUND_MESSAGE)

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


def test_document_library_index_is_cached(monkeypatch):
    """Library index should be cached to avoid repeated filesystem scans."""

    state = {"cache": None, "calls": 0}

    def fake_get(_key):
        return state["cache"]

    def fake_set(_key, value, timeout):
        state["cache"] = value
        state["timeout"] = timeout

    def fake_collect(_root_base):
        state["calls"] += 1
        return [{"title": "Docs", "items": []}]

    monkeypatch.setattr(views.cache, "get", fake_get)
    monkeypatch.setattr(views.cache, "set", fake_set)
    monkeypatch.setattr(views, "_collect_document_library", fake_collect)

    first = views._get_cached_document_library(Path("/tmp/project"))
    second = views._get_cached_document_library(Path("/tmp/project"))

    assert first == second
    assert state["calls"] == 1
    assert state["timeout"] == views.DOCUMENT_LIBRARY_CACHE_TIMEOUT
