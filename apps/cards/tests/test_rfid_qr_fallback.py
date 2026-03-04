from __future__ import annotations

from types import SimpleNamespace

from apps.cards.models import RFID
from apps.cards.models import rfid as rfid_module


def test_qr_test_link_returns_blank_when_qrcode_missing(monkeypatch):
    """QR preview generation should degrade gracefully without qrcode installed."""

    monkeypatch.setattr(rfid_module, "_load_qrcode_module", lambda: None)

    tag = RFID(rfid="DEADBEEF")

    assert tag.qr_test_link() == ""


def test_qr_test_link_returns_link_when_qrcode_available(monkeypatch):
    """QR preview generation should work when a qrcode-like module is available."""

    class StubImage:
        def save(self, buffer, format):
            assert format == "PNG"
            buffer.write(b"stub-png-bytes")

    class StubQRCode:
        def __init__(self, *, box_size, border):
            self.box_size = box_size
            self.border = border
            self.data = None

        def add_data(self, data):
            self.data = data

        def make(self, fit):
            assert fit is True

        def make_image(self, *, fill_color, back_color):
            assert fill_color == "black"
            assert back_color == "white"
            return StubImage()

    monkeypatch.setattr(
        rfid_module,
        "_load_qrcode_module",
        lambda: SimpleNamespace(QRCode=StubQRCode),
    )

    tag = RFID(rfid="DEADBEEF")

    assert tag.qr_test_link() != ""
