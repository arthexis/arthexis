"""Regression coverage for the links reference unique-constraint migration."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.regression]


def _create_term_linked_to_reference(project_apps, reference):
    """Create a historical ``Term`` row linked to ``reference`` when available."""

    try:
        Term = project_apps.get_model("terms", "Term")
    except LookupError:
        return None

    return Term.objects.create(
        title="SQLite Terms",
        slug="sqlite-terms",
        reference=reference,
    )


def test_reference_constraint_migration_deduplicates_existing_rows() -> None:
    """Migration 0017 should remove duplicate pairs before adding the constraint."""

    from_state = [("links", "0016_reference_application_and_seed_links")]
    to_state = [("links", "0017_reference_links_reference_alt_text_value_uniq")]

    executor = MigrationExecutor(connection)
    executor.migrate(from_state)
    apps = executor.loader.project_state(from_state).apps
    Reference = apps.get_model("links", "Reference")

    keep = Reference.objects.create(alt_text="SQLite", value="https://www.sqlite.org/")
    duplicate = Reference.objects.create(alt_text="SQLite", value="https://www.sqlite.org/")
    created_term = _create_term_linked_to_reference(apps, duplicate)

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

    if created_term is not None:
        MigratedTerm = migrated_apps.get_model("terms", "Term")
        migrated_term = MigratedTerm.objects.get(pk=created_term.pk)
        assert migrated_term.reference_id == keep.id
