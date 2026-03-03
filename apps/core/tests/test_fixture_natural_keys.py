"""Low-value fixture shape checks were removed in favor of behavior-level coverage."""

from __future__ import annotations

import json
from pathlib import Path


def test_core_fixtures_avoid_primary_key_fields():
    """Regression: core fixtures should continue using natural keys instead of explicit PKs."""

    fixtures_dir = Path(__file__).resolve().parents[1] / "fixtures"
    fixtures = sorted(fixtures_dir.glob("*.json"))

    assert fixtures

    offenders: dict[str, list[int]] = {}
    for fixture in fixtures:
        entries = json.loads(fixture.read_text())
        for index, entry in enumerate(entries, start=1):
            if isinstance(entry, dict) and "pk" in entry:
                offenders.setdefault(fixture.name, []).append(index)

    assert offenders == {}
