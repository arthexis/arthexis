from __future__ import annotations

from apps.cards.models import RFID
from apps.cards.models import rfid as rfid_module


def test_qr_test_link_returns_blank_when_qrcode_missing(monkeypatch):
    """QR preview generation should degrade gracefully without qrcode installed."""

    monkeypatch.setattr(rfid_module, "_load_qrcode_module", lambda: None)

    tag = RFID(rfid="DEADBEEF")

    assert tag.qr_test_link() == ""
