from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from apps.imager.node_features import check_node_feature


def _node_with_role(role_name: str) -> SimpleNamespace:
    return SimpleNamespace(role=SimpleNamespace(name=role_name))


def test_imager_burner_feature_auto_enables_on_control_with_lsblk(monkeypatch):
    monkeypatch.setattr("apps.imager.burner.os.name", "posix")
    monkeypatch.setattr("apps.imager.burner.shutil.which", lambda name: "/usr/bin/lsblk")

    assert (
        check_node_feature(
            "imager-burner",
            node=_node_with_role("Control"),
            base_dir=Path("."),
            base_path=Path("."),
        )
        is True
    )


def test_imager_burner_feature_does_not_enable_on_terminal(monkeypatch):
    monkeypatch.setattr("apps.imager.burner.os.name", "posix")
    monkeypatch.setattr("apps.imager.burner.shutil.which", lambda name: "/usr/bin/lsblk")

    assert (
        check_node_feature(
            "imager-burner",
            node=_node_with_role("Terminal"),
            base_dir=Path("."),
            base_path=Path("."),
        )
        is False
    )
