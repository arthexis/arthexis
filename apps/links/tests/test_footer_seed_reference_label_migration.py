"""Tests for footer seed reference label shortening migration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from apps.links.models import Reference

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize(
    ("old_alt_text", "value", "new_alt_text"),
    [
        ("The Python Foundation", "https://www.python.org/", "The Foundation"),
        (
            "Open Charge Point Protocol",
            "https://openchargealliance.org/protocols/open-charge-point-protocol/",
            "Open CP Protocol",
        ),
        (
            "GitHub Repositories",
            "https://github.com/orgs/arthexis/repositories",
            "GitHub Repos",
        ),
        (
            "ARG 1.0 (The Arthexis Reciprocity License)",
            "https://github.com/arthexis/arthexis/blob/main/LICENSE",
            "ARG License 1.0",
        ),
        ("Wizards of the Coast", "https://company.wizards.com/", "The Wizards"),
    ],
)
def test_footer_seed_reference_label_migration_updates_existing_seed_rows(
    old_alt_text: str,
    value: str,
    new_alt_text: str,
) -> None:
    migration = importlib.import_module(
        "apps.links.migrations.0004_shorten_footer_reference_labels"
    )

    stale = Reference.objects.create(
        alt_text=old_alt_text,
        value=value,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )
    duplicate = Reference.objects.create(
        alt_text=new_alt_text,
        value=value,
        include_in_footer=True,
        is_seed_data=True,
        method="link",
    )

    migration.shorten_footer_seed_reference_labels(
        apps=type("Apps", (), {"get_model": staticmethod(lambda *_: Reference)}),
        schema_editor=SimpleNamespace(connection=SimpleNamespace(alias="default")),
    )

    stale.refresh_from_db()
    assert stale.alt_text == new_alt_text
    assert stale.value == value
    assert not Reference.objects.filter(pk=duplicate.pk).exists()

