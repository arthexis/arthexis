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
        lambda *, node=None: {"detected": True},
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
        lambda *, node=None: expected,
    )

    assert detect.detect_scanner() == expected
