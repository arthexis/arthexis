"""Regression tests for role default upgrade policy fixture mappings."""

from __future__ import annotations

import json
from pathlib import Path


def _load_role_fixture(role_slug: str) -> dict[str, object]:
    """Load a node-role fixture by slug and return its fields mapping."""

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / f"node_roles__noderole_{role_slug}.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return payload[0]["fields"]


def test_control_watchtower_terminal_default_to_stable_channel_policy() -> None:
    """Control, Watchtower, and Terminal fixtures should default to Stable policy."""

    for role_slug in ("control", "watchtower", "terminal"):
        fields = _load_role_fixture(role_slug)
        assert fields.get("default_upgrade_policy") == ["Stable"]
