"""Regression tests for docs canonical URLs and full-content defaults."""

from pathlib import Path
from types import SimpleNamespace

from django.http import HttpResponse
from django.test import RequestFactory

from apps.docs import views


def test_should_default_full_document_for_install_lifecycle_manual():
    """Operational manuals should default to full-content rendering."""

    assert views._should_default_full_document("docs/development/install-lifecycle-scripts-manual.md")
    assert not views._should_default_full_document("docs/development/ocpp-user-manual.md")


def test_build_canonical_url_normalizes_mobile_docs_host():
    """Docs canonical URLs should point to the primary host when aliases are used."""

    request = RequestFactory().get("/docs/development/install-lifecycle-scripts-manual")
    request.META["HTTP_HOST"] = "m.arthexis.com"

    canonical_url = views._build_canonical_url(request)

    assert canonical_url == "http://arthexis.com/docs/development/install-lifecycle-scripts-manual"


def test_render_readme_page_uses_full_content_for_operational_manual(monkeypatch):
    """Install lifecycle manual should render fully without lazy fragments."""

    request = RequestFactory().get("/docs/development/install-lifecycle-scripts-manual")
    request.META["HTTP_HOST"] = "m.arthexis.com"
    request.user = SimpleNamespace(is_authenticated=False, is_staff=False)
    request.LANGUAGE_CODE = "en-us"
    captured_context: dict[str, object] = {}

    def _fake_locate_document(*_args, **_kwargs):
        return SimpleNamespace(file=Path("/tmp/install-lifecycle-scripts-manual.md"), title="Install Manual")

    def _fake_render_document_file(_path):
        return "<h1>Install</h1><p>Step one.</p>", "<ul><li>Install</li></ul>"

    def _fake_split_html_sections(_html: str, _max_sections: int):
        return "<h1>Install</h1><p>Step one.</p>", "<p>Deferred section.</p>"

    def _fake_render(_request, _template, context, status=200):
        captured_context.update(context)
        return HttpResponse("ok", status=status)

    monkeypatch.setattr(views, "_locate_readme_document", _fake_locate_document)
    monkeypatch.setattr(views.rendering, "render_document_file", _fake_render_document_file)
    monkeypatch.setattr(views.rendering, "split_html_sections", _fake_split_html_sections)
    monkeypatch.setattr(views, "render", _fake_render)

    response = views.render_readme_page(request, doc="development/install-lifecycle-scripts-manual", prepend_docs=True, role=object())

    assert response.status_code == 200
    assert captured_context["has_remaining_sections"] is False
    assert captured_context["content"] == "<h1>Install</h1><p>Step one.</p>"
    assert captured_context["canonical_url"] == (
        "http://arthexis.com/docs/development/install-lifecycle-scripts-manual?full=1"
    )
