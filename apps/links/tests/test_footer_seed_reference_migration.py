"""Tests for footer seed reference key migration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from apps.links.models import Reference


pytestmark = pytest.mark.django_db


def test_footer_seed_reference_key_migration_updates_existing_seed_rows() -> None:
    migration = importlib.import_module(
        "apps.links.migrations.0003_update_footer_reference_seed_keys"
    )

    stale = Reference.objects.create(
        alt_text="GNU GPLv3",
        value="https://www.gnu.org/licenses/gpl-3.0.en.html",
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )
    duplicate = Reference.objects.create(
        alt_text="ARG 1.0 (The Arthexis Reciprocity License)",
        value="https://github.com/arthexis/arthexis/blob/main/LICENSE",
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )

    migration.update_footer_seed_references(
        apps=type("Apps", (), {"get_model": staticmethod(lambda *_: Reference)}),
        schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    stale.refresh_from_db()
    assert stale.alt_text == "ARG 1.0 (The Arthexis Reciprocity License)"
    assert stale.value == "https://github.com/arthexis/arthexis/blob/main/LICENSE"
    assert not Reference.objects.filter(pk=duplicate.pk).exists()
