"""Regression checks for default admin badge seed data."""

from __future__ import annotations

import json
from pathlib import Path


EXPECTED_PROVIDERS = {"site", "node", "role"}


def test_default_admin_badge_seed_fixture_covers_all_default_providers() -> None:
    """New installs should receive default Site/Node/Role admin badges."""

    fixture_path = Path("apps/sites/fixtures/admin_badge__default.json")
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    providers = {
        entry.get("fields", {}).get("provider_key")
        for entry in payload
        if entry.get("model") == "pages.adminbadge"
        and entry.get("fields", {}).get("is_enabled") is True
    }

    assert providers == EXPECTED_PROVIDERS
