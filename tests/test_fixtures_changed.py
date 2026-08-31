"""Tests for fixture reload change detection helpers."""

from __future__ import annotations

from scripts.fixtures_changed import (
    GLOBAL_FIXTURE_BUCKET,
    changed_fixture_app_labels,
    fixture_app_label,
    fixtures_changed,
    select_changed_app_fixtures,
)


def test_fixture_app_label_uses_app_fixture_bucket() -> None:
    assert (
        fixture_app_label("apps/features/fixtures/features__imager_burner.json")
        == "features"
    )
    assert fixture_app_label("config/fixtures/sites.json") == GLOBAL_FIXTURE_BUCKET


def test_changed_fixture_app_labels_identifies_only_changed_buckets() -> None:
    assert changed_fixture_app_labels(
        current_by_app={"features": "new", "modules": "same"},
        stored_by_app={"features": "old", "modules": "same"},
    ) == {"features"}


def test_select_changed_app_fixtures_limits_known_app_changes() -> None:
    fixtures = [
        "apps/modules/fixtures/modules__module_alpha.json",
        "apps/features/fixtures/features__imager_burner.json",
        "apps/features/fixtures/features__imager_writer.json",
    ]

    assert select_changed_app_fixtures(fixtures, {"features"}) == [
        "apps/features/fixtures/features__imager_burner.json",
        "apps/features/fixtures/features__imager_writer.json",
    ]


def test_select_changed_app_fixtures_falls_back_for_global_changes() -> None:
    fixtures = [
        "apps/features/fixtures/features__imager_burner.json",
        "config/fixtures/sites.json",
    ]

    assert select_changed_app_fixtures(
        fixtures,
        {GLOBAL_FIXTURE_BUCKET},
    ) == fixtures


def test_select_changed_app_fixtures_falls_back_when_no_files_match() -> None:
    fixtures = ["apps/features/fixtures/features__imager_burner.json"]

    assert select_changed_app_fixtures(fixtures, {"removed_app"}) == fixtures


def test_fixtures_changed_uses_per_app_cache_difference() -> None:
    assert fixtures_changed(
        fixtures_present=True,
        current_hash="same-overall",
        stored_hash="same-overall",
        migrations_changed=False,
        migrations_ran=False,
        current_by_app={"features": "new"},
        stored_by_app={"features": "old"},
        clean=False,
    )
