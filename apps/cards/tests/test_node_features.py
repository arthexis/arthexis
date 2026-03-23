from __future__ import annotations

from pathlib import Path

import pytest

from apps.cards import detect
from apps.cards import node_features


@pytest.mark.django_db
def test_setup_node_feature_writes_compatibility_lock(monkeypatch, settings, tmp_path):
    """RFID setup should keep lock-file compatibility within Python hooks."""

    settings.BASE_DIR = str(tmp_path)

    class StubNode:
        def get_base_path(self) -> Path:
            return tmp_path

    monkeypatch.setattr(
        node_features,
        "detect_scanner_capability",
        lambda *, node=None, base_dir=None, base_path=None: {"detected": True},
    )

    result = node_features.setup_node_feature("rfid-scanner", node=StubNode())

    assert result is True
    assert (tmp_path / ".locks" / "rfid.lck").exists()


@pytest.mark.django_db
def test_detect_scanner_reuses_node_feature_detection(monkeypatch):
    """Legacy detection CLI should delegate to node feature detection logic."""

    expected = {"detected": True, "assumed": True, "reason": "test"}
    monkeypatch.setattr(
        node_features,
        "detect_scanner_capability",
        lambda *, node=None, base_dir=None, base_path=None: expected,
    )

    assert detect.detect_scanner() == expected


@pytest.mark.django_db
def test_detect_scanner_ignores_stale_compatibility_lock(monkeypatch, settings, tmp_path):
    """Stale lock files should not short-circuit detection as available hardware."""

    settings.BASE_DIR = str(tmp_path)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    stale_lock = lock_dir / "rfid.lck"
    stale_lock.write_text("stale")

    monkeypatch.setattr(
        node_features,
        "_service_detection",
        lambda *, base_dir=None: {"detected": False, "reason": "RFID scanner not detected"},
    )

    class StubNode:
        def get_base_path(self) -> Path:
            return tmp_path

    class _BackgroundReader:
        @staticmethod
        def lock_file_active():
            stale_lock.unlink(missing_ok=True)
            return False, stale_lock

    import sys

    monkeypatch.setitem(sys.modules, "apps.cards.background_reader", _BackgroundReader)
    monkeypatch.setitem(
        sys.modules,
        "apps.cards.irq_wiring_check",
        type("_IRQCheck", (), {"check_irq_pin": staticmethod(lambda: {"error": "no irq"})}),
    )

    result = node_features.detect_scanner_capability(node=StubNode())

    assert result == {"detected": False, "reason": "no irq"}
    assert not stale_lock.exists()
