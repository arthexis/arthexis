"""Regression tests for documentation fallback rendering."""

from pathlib import Path

from django.http import Http404, HttpResponse
from django.test import RequestFactory

from apps.docs import views

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


def test_extract_document_blurb_reads_first_content_line(tmp_path: Path):
    """Regression: library blurbs should skip headings and use the first body paragraph."""

    doc = tmp_path / "guide.md"
    doc.write_text("# Heading\n\nFirst useful sentence.\nSecond sentence.", encoding="utf-8")

    blurb = views._extract_document_blurb(doc, max_length=80)

    assert blurb == "First useful sentence. Second sentence."


def test_collect_document_library_includes_item_blurbs(tmp_path: Path, monkeypatch):
    """Regression: each document library item should include a short description blurb."""

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "guide.md").write_text("Guide summary line.", encoding="utf-8")

    apps_docs_dir = tmp_path / "apps" / "docs"
    apps_docs_dir.mkdir(parents=True)
    (apps_docs_dir / "cookbook.md").write_text("Cookbook summary line.", encoding="utf-8")

    monkeypatch.setattr(views, "reverse", lambda route, args: f"/{route}/{args[0]}")

    sections = views._collect_document_library(tmp_path)

    assert sections
    for section in sections:
        for item in section["items"]:
            assert "description" in item
            assert item["description"]


def test_extract_document_blurb_skips_yaml_front_matter(tmp_path: Path):
    """Regression: YAML front matter should be skipped and first content paragraph extracted."""

    doc = tmp_path / "frontmatter.md"
    doc.write_text(
        "---\ntitle: Example\nsummary: ignore this metadata\n---\n\n# Heading\n\nUseful intro sentence.",
        encoding="utf-8",
    )

    blurb = views._extract_document_blurb(doc)

    assert blurb == "Useful intro sentence."


def test_extract_document_blurb_truncates_at_word_boundary(tmp_path: Path):
    """Truncation should preserve full words when possible."""

    doc = tmp_path / "long.md"
    doc.write_text("Alpha beta gamma delta epsilon", encoding="utf-8")

    blurb = views._extract_document_blurb(doc, max_length=16)

    assert blurb == "Alpha beta…"
