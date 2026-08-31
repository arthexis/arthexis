"""Helpers for determining when fixtures need to be reloaded."""

from __future__ import annotations


GLOBAL_FIXTURE_BUCKET = "global"


def fixture_app_label(fixture: str) -> str:
    """Return the app bucket used for a project fixture path."""

    parts = fixture.split("/")
    if len(parts) >= 4 and parts[0] == "apps" and parts[2] == "fixtures":
        return parts[1]
    return GLOBAL_FIXTURE_BUCKET


def changed_fixture_app_labels(
    *,
    current_by_app: dict[str, str],
    stored_by_app: dict[str, str],
) -> set[str]:
    """Return app labels whose fixture hashes changed."""

    labels = set(current_by_app) | set(stored_by_app)
    return {
        label
        for label in labels
        if current_by_app.get(label) != stored_by_app.get(label)
    }


def select_changed_app_fixtures(
    fixtures: list[str],
    changed_app_labels: set[str],
) -> list[str]:
    """Return fixtures that belong to changed app buckets.

    Unknown/global fixture changes fall back to the full fixture list because
    they may contain cross-app seed data.
    """

    if not changed_app_labels or GLOBAL_FIXTURE_BUCKET in changed_app_labels:
        return list(fixtures)

    selected = [
        fixture
        for fixture in fixtures
        if fixture_app_label(fixture) in changed_app_labels
    ]
    return selected or list(fixtures)


def fixtures_changed(
    *,
    fixtures_present: bool,
    current_hash: str,
    stored_hash: str,
    migrations_changed: bool,
    migrations_ran: bool,
    current_by_app: dict[str, str] | None = None,
    stored_by_app: dict[str, str] | None = None,
    clean: bool,
) -> bool:
    """Return ``True`` when fixtures should be reloaded.

    Reloads occur when fixtures exist and one of the following is true:
    * the ``--clean`` flag was provided,
    * migrations changed or ran since the last refresh, or
    * the fixture hash differs from the stored value.
    """

    if not fixtures_present:
        return False

    if clean or migrations_changed or migrations_ran:
        return True

    if current_by_app is not None and stored_by_app is not None:
        if current_by_app != stored_by_app:
            return True

    return current_hash != stored_hash
