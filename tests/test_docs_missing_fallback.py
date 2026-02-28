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

def test_collect_document_library_skips_items_when_reverse_fails(tmp_path: Path, monkeypatch):
    """Unresolvable routes should not break library generation or caching."""

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "guide.md").write_text("Guide summary line.", encoding="utf-8")

    def fake_reverse(_route, args):
        raise views.NoReverseMatch("boom")

    monkeypatch.setattr(views, "reverse", fake_reverse)

    sections = views._collect_document_library(tmp_path)

    assert sections == [{"title": "Documentation", "items": []}]
