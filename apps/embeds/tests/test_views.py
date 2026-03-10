"""Tests for embeds view helpers."""

from __future__ import annotations

import importlib

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
