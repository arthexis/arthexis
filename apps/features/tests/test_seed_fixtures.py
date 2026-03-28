"""Regression checks for suite feature seed fixture coverage."""

from __future__ import annotations

import json
from pathlib import Path


REQUIRED_SUITE_FEATURE_SLUGS = {
    "celery-workers",
    "energy-accounts",
    "evergo-api-client",
    "feedback-ingestion",
    "github-issue-reporting",
    "llm-summary-suite",
    "ocpp-16-charge-point",
    "ocpp-201-charge-point",
    "ocpp-21-charge-point",
    "ocpp-forwarder",
    "ocpp-ftp-reports",
    "ocpp-simulator",
    "odoo-crm-sync",
    "operator-site-interface",
    "pages-chat",
    "playwright-automation",
    "release-management",
    "rfid-auth-audit",
    "screenshot-capture",
    "shortcut-management",
    "staff-chat-bridge",
    "standard-charge-point",
    "usage-analytics",
    "whatsapp-chat-bridge",
}


def _feature_fixture_slugs() -> set[str]:
    fixtures_dir = Path("apps/features/fixtures")
    slugs: set[str] = set()

    for path in sorted(fixtures_dir.glob("features__*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload:
            if entry.get("model") != "features.feature":
                continue
            fields = entry.get("fields") or {}
            slug = fields.get("slug")
            if isinstance(slug, str) and slug:
                slugs.add(slug)

    return slugs


def test_suite_feature_seed_fixtures_cover_runtime_required_slugs() -> None:
    """New installs should seed all mainstream suite features needed at runtime."""

    fixture_slugs = _feature_fixture_slugs()
    missing = sorted(REQUIRED_SUITE_FEATURE_SLUGS - fixture_slugs)
    assert missing == []
