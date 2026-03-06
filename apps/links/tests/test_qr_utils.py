"""Tests for QR utility rendering helpers."""

from __future__ import annotations

import builtins
import sys

import pytest

from apps.links import qr_utils


@pytest.mark.parametrize("generator", [qr_utils.build_qr_png_bytes, qr_utils.build_qr_svg_text])
def test_qr_utils_raise_clear_error_when_qrcode_missing(monkeypatch, generator):
    """Regression: loading links app should not fail before QR helpers are used."""

    real_import = builtins.__import__

    # Clear cached qrcode modules so the import hook is exercised deterministically.
    for mod_name in list(sys.modules):
        if mod_name == "qrcode" or mod_name.startswith("qrcode."):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    def fail_qrcode(name, *args, **kwargs):
        if name == "qrcode" or name.startswith("qrcode."):
            raise ModuleNotFoundError("No module named 'qrcode'", name="qrcode")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_qrcode)

    with pytest.raises(RuntimeError, match="Install project optional dependency group 'nodes'"):
        generator("https://example.com")


def test_qr_svg_and_png_generation_returns_data():
    """Ensure QR utility functions return non-empty payloads for valid URLs."""

    png_bytes = qr_utils.build_qr_png_bytes("https://example.com")
    svg_text = qr_utils.build_qr_svg_text("https://example.com")

    assert png_bytes.startswith(b"\x89PNG")
    assert svg_text.lstrip().startswith("<?xml")
