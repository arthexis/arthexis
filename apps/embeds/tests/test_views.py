"""Tests for embeds view helpers."""

from __future__ import annotations

import importlib

from django.http import HttpResponse
from django.template import loader
from django.test import RequestFactory

from apps.embeds import views


def test_encode_qr_image_returns_empty_when_qrcode_missing(monkeypatch) -> None:
    """QR generation should degrade gracefully when qrcode is unavailable."""

    real_import_module = importlib.import_module

    def _import_module(name: str):
        if name == "qrcode":
            raise ImportError("qrcode is not installed")
        return real_import_module(name)

    monkeypatch.setattr(views.importlib, "import_module", _import_module)

    assert views._encode_qr_image("https://example.com") == ""


def test_embed_card_hides_qr_image_when_qrcode_missing(monkeypatch) -> None:
    """Embed card should render without a broken QR image when qrcode is missing."""

    real_import_module = importlib.import_module

    def _import_module(name: str):
        if name == "qrcode":
            raise ImportError("qrcode is not installed")
        return real_import_module(name)

    def _render_without_request_context(_request, template_name: str, context: dict):
        html = loader.render_to_string(template_name, context=context)
        return HttpResponse(html)

    monkeypatch.setattr(views.importlib, "import_module", _import_module)
    monkeypatch.setattr(views.EmbedLead.objects, "create", lambda **kwargs: None)
    monkeypatch.setattr(views, "render", _render_without_request_context)

    request = RequestFactory().get(
        "/embeds/",
        {"target": "https://example.com/path"},
        HTTP_HOST="localhost",
    )
    response = views.embed_card(request)

    content = response.content.decode("utf-8")

    assert response.status_code == 200
    assert 'href="https://example.com/path"' in content
    assert "data:image/png;base64," not in content
