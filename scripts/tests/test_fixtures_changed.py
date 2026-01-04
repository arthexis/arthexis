"""Tests for fixtures change detection used by env-refresh."""

from scripts.fixtures_changed import fixtures_changed


def test_fixtures_changed_when_fixture_hash_differs() -> None:
    """A different fixture hash triggers a reload."""

    assert fixtures_changed(
        fixtures_present=True,
        current_hash="new_hash",
        stored_hash="old_hash",
        migrations_changed=False,
        migrations_ran=False,
        clean=False,
    )


def test_fixtures_changed_when_migrations_changed() -> None:
    """Migrations changing should trigger fixture reloads even with same hash."""

    assert fixtures_changed(
        fixtures_present=True,
        current_hash="shared_hash",
        stored_hash="shared_hash",
        migrations_changed=True,
        migrations_ran=False,
        clean=False,
    )
