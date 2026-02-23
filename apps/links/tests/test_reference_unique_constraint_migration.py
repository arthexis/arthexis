"""Regression coverage for the links reference unique-constraint migration."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.regression]


def test_reference_constraint_migration_deduplicates_existing_rows() -> None:
    """Migration 0017 should remove duplicate pairs before adding the constraint."""

    from_state = [("links", "0016_reference_application_and_seed_links")]
    to_state = [("links", "0017_reference_links_reference_alt_text_value_uniq")]

    executor = MigrationExecutor(connection)
    executor.migrate(from_state)
    apps = executor.loader.project_state(from_state).apps
    Reference = apps.get_model("links", "Reference")

    Reference.objects.create(alt_text="SQLite", value="https://www.sqlite.org/")
    Reference.objects.create(alt_text="SQLite", value="https://www.sqlite.org/")

    executor = MigrationExecutor(connection)
    executor.migrate(to_state)
    migrated_apps = executor.loader.project_state(to_state).apps
    MigratedReference = migrated_apps.get_model("links", "Reference")

    assert (
        MigratedReference.objects.filter(
            alt_text="SQLite", value="https://www.sqlite.org/"
        ).count()
        == 1
    )
