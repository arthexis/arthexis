from __future__ import annotations

from pathlib import Path

import pytest

from apps.cards import detect, node_features


@pytest.mark.django_db
def test_setup_node_feature_writes_compatibility_lock(monkeypatch, settings, tmp_path):
    """RFID setup should write the scanner service lock file."""

    settings.BASE_DIR = str(tmp_path)

    class StubNode:
        def get_base_path(self) -> Path:
            return tmp_path

    monkeypatch.setattr(
        node_features,
        "detect_scanner_capability",
        lambda *, node=None, base_dir=None, base_path=None: {"detected": True},
    )

    result = node_features.setup_node_feature(
        "rfid-scanner",
        node=StubNode(),
        base_dir=tmp_path,
        base_path=tmp_path,
    )

    assert result is True
    assert (tmp_path / ".locks" / "rfid-service.lck").exists()


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
def test_detect_scanner_control_role_assumes_service():
    """Control nodes should always advertise scanner service availability."""

    class StubRole:
        name = "Control"

    class StubNode:
        role = StubRole()

    result = node_features.detect_scanner_capability(node=StubNode())

    assert result["detected"] is True
    assert result["assumed"] is True
