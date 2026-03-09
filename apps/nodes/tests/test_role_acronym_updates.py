"""Regression coverage for updated Terminal/Satellite role acronyms."""

from __future__ import annotations

import importlib
from pathlib import Path
import json

import pytest
from django.apps import apps as django_apps

from apps.nodes.models import NodeRole

migration_0042 = importlib.import_module(
    "apps.nodes.migrations.0042_update_terminal_satellite_acronyms"
)


@pytest.mark.django_db
def test_0042_forward_and_reverse_acronym_updates() -> None:
    """Migration helpers should swap acronyms forward and backward safely."""

    terminal = NodeRole.objects.create(name="Terminal", acronym="TERM")
    satellite = NodeRole.objects.create(name="Satellite", acronym="SATL")

    migration_0042.forward_update_acronyms(django_apps, None)

    terminal.refresh_from_db()
    satellite.refresh_from_db()
    assert terminal.acronym == "TRMN"
    assert satellite.acronym == "STLT"

    migration_0042.reverse_update_acronyms(django_apps, None)

    terminal.refresh_from_db()
    satellite.refresh_from_db()
    assert terminal.acronym == "TERM"
    assert satellite.acronym == "SATL"


def _load_role_fixture(role_slug: str) -> dict[str, object]:
    """Load a role fixture payload and return its field mapping."""

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / f"node_roles__noderole_{role_slug}.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return payload[0]["fields"]


def test_terminal_and_satellite_fixture_acronyms_are_updated() -> None:
    """Fixtures should ship with the updated node role acronyms."""

    assert _load_role_fixture("terminal").get("acronym") == "TRMN"
    assert _load_role_fixture("satellite").get("acronym") == "STLT"
