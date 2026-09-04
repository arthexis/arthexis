from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection

from tests.gate_markers import gate

pytestmark = [gate.upgrade]


def _pages_landing_columns() -> set[str]:
    with connection.cursor() as cursor:
        return {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, "pages_landing"
            )
        }


def test_drop_stale_landing_track_leads_migration_depends_on_pages_initial() -> None:
    migration = import_module(
        "apps.sites.migrations.0003_drop_stale_landing_track_leads"
    )

    assert ("pages", "0002_initial") in migration.Migration.dependencies


@pytest.mark.django_db(transaction=True)
def test_drop_stale_landing_track_leads_column() -> None:
    migration = import_module(
        "apps.sites.migrations.0003_drop_stale_landing_track_leads"
    )

    with connection.schema_editor() as schema_editor:
        if "track_leads" not in _pages_landing_columns():
            schema_editor.execute(
                'ALTER TABLE "pages_landing" '
                'ADD COLUMN "track_leads" bool NOT NULL DEFAULT 0'
            )

    assert "track_leads" in _pages_landing_columns()

    with connection.schema_editor() as schema_editor:
        migration.drop_stale_landing_track_leads(None, schema_editor)

    assert "track_leads" not in _pages_landing_columns()

    with connection.schema_editor() as schema_editor:
        migration.drop_stale_landing_track_leads(None, schema_editor)

    assert "track_leads" not in _pages_landing_columns()
