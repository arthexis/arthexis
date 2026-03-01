"""Route-focused regression tests for the public docs URL surface."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.urls import resolve, reverse

from apps.docs import views



@pytest.fixture
def request_factory() -> RequestFactory:
    """Create requests for direct URL resolution without middleware side effects."""

    return RequestFactory()


@pytest.fixture
def deterministic_docs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Provide deterministic docs rendering and library discovery for route tests."""

    document_file = tmp_path / "docs" / "guide.md"
    document_file.parent.mkdir(parents=True)
    document_file.write_text("# Deterministic Guide\n\nBody.", encoding="utf-8")

    def fake_locate(_role, doc: str | None, _lang: str) -> SimpleNamespace:
        normalized = (doc or "").replace("\\", "/")
        if doc and (".." in normalized.split("/") or normalized.startswith("/")):
            raise Http404(views.DOCUMENT_NOT_FOUND_MESSAGE)

        allowed_docs = {
            None,
            "",
            "README.md",
            "docs/guide.md",
            "guide.md",
            "cookbooks/favorites.md",
        }
        if doc not in allowed_docs:
            raise Http404(views.DOCUMENT_NOT_FOUND_MESSAGE)

        return SimpleNamespace(file=document_file, title="Deterministic Guide", root_base=tmp_path)

    monkeypatch.setattr(views, "_locate_readme_document", fake_locate)
    monkeypatch.setattr(views.Node, "get_local", lambda: None)
    monkeypatch.setattr(
        views.rendering,
        "render_document_file",
        lambda _path: ("<h1>Deterministic Guide</h1><p>Rendered.</p>", "<ul><li>Deterministic Guide</li></ul>"),
    )
    monkeypatch.setattr(views.rendering, "split_html_sections", lambda html, _max_sections: (html, ""))
    monkeypatch.setattr(
        views,
        "_get_cached_document_library",
        lambda _root: [
            {
                "title": "Deterministic Section",
                "items": [{"label": "guide.md", "url": "/docs/guide.md"}],
            }
        ],
    )

    def fake_render(_request, _template, _context, status=200):
        """Render deterministic HTML without DB-dependent context processors."""

        return views.HttpResponse("deterministic render", status=status)

    render_mock = Mock(side_effect=fake_render)
    monkeypatch.setattr(views, "render", render_mock)

    return {"document_file": document_file, "render_mock": render_mock}


def _dispatch_url(request_factory: RequestFactory, url: str):
    """Resolve a URL and call the matched view directly."""

    request = request_factory.get(url)
    request.user = SimpleNamespace(is_authenticated=False)
    match = resolve(url)
    return match.func(request, *match.args, **match.kwargs)


def test_docs_library_route_returns_expected_marker(request_factory, deterministic_docs):
    """Regression: docs library route should render a stable section and title."""

    response = _dispatch_url(request_factory, reverse("docs:docs-library"))
    render_context = deterministic_docs["render_mock"].call_args.args[2]

    assert response.status_code == 200
    assert render_context["title"] == "Developer Documents"
    assert render_context["sections"][0]["title"] == "Deterministic Section"


@pytest.mark.parametrize(
    ("url_name", "url_args"),
    [
        ("docs:readme", None),
        ("docs:docs-index", None),
        ("docs:docs-document", ["guide.md"]),
    ],
)
def test_public_read_routes_render_successfully(request_factory, deterministic_docs, url_name: str, url_args):
    """Regression: readme, docs index, and docs document routes should all render successfully."""

    url = reverse(url_name, args=url_args)
    response = _dispatch_url(request_factory, url)
    render_context = deterministic_docs["render_mock"].call_args.args[2]

    assert response.status_code == 200
    assert render_context["title"] == "Deterministic Guide"


def test_missing_document_returns_library_fallback_message(request_factory, deterministic_docs):
    """Regression: missing docs document URLs should fall back to the library page with context messaging."""

    response = _dispatch_url(request_factory, reverse("docs:docs-document", args=["missing.md"]))
    render_context = deterministic_docs["render_mock"].call_args.args[2]

    assert response.status_code == 404
    assert render_context["title"] == "Developer Documents"
    assert render_context["missing_document"] == "docs/missing.md"


@pytest.mark.parametrize("attempt", ["../secrets.md", "..%2Fsecrets.md", "%2Fetc%2Fpasswd"])
def test_path_traversal_attempts_return_404(
    request_factory,
    deterministic_docs,
    attempt: str,
):
    """Regression: traversal and absolute-like paths should not be readable through docs routes."""

    response = _dispatch_url(request_factory, reverse("docs:docs-index") + attempt)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("asset_name", "expected_status", "expected_content_type"),
    [
        ("allowed.png", 200, "image/png"),
        ("disallowed.txt", 404, None),
        ("missing.png", 404, None),
    ],
)
def test_readme_asset_serves_allowed_and_rejects_disallowed_or_missing(
    request_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    asset_name: str,
    expected_status: int,
    expected_content_type: str | None,
):
    """Regression: readme-asset should serve image assets and reject missing/disallowed files."""

    allowed = tmp_path / "assets" / "ok.png"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"png")

    disallowed = tmp_path / "assets" / "bad.txt"
    disallowed.write_text("nope", encoding="utf-8")

    missing = tmp_path / "assets" / "missing.png"

    mapping = {
        "allowed.png": allowed,
        "disallowed.txt": disallowed,
        "missing.png": missing,
    }

    def fake_resolve_static_asset(asset: str) -> Path:
        """Resolve static assets deterministically for route checks."""

        return mapping[asset]

    monkeypatch.setattr("apps.docs.assets.resolve_static_asset", fake_resolve_static_asset)

    url = reverse("docs:readme-asset", args=["static", asset_name])
    if expected_status == 404:
        with pytest.raises(Http404):
            _dispatch_url(request_factory, url)
        return

    response = _dispatch_url(request_factory, url)
    assert response.status_code == expected_status
    if expected_content_type:
        assert response["Content-Type"] == expected_content_type
