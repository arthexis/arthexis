from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps

from apps.features.models import Feature, FeatureTest


migration_module = importlib.import_module("apps.features.migrations.0005_merge_evergo_feature_flags")


@pytest.mark.django_db
def test_merge_evergo_features_deduplicates_conflicting_feature_tests() -> None:
    canonical = Feature.objects.create(
        slug="evergo-api-client",
        display="Canonical",
        source=Feature.Source.MAINSTREAM,
    )
    legacy = Feature.objects.create(
        slug="evergo-integration",
        display="Legacy",
        source=Feature.Source.MAINSTREAM,
    )

    FeatureTest.objects.create(
        feature=canonical,
        node_id="tests/test_sync.py::test_duplicate_node",
        name="Canonical guard",
    )
    FeatureTest.objects.create(
        feature=legacy,
        node_id="tests/test_sync.py::test_duplicate_node",
        name="Legacy duplicate",
    )
    moved = FeatureTest.objects.create(
        feature=legacy,
        node_id="tests/test_sync.py::test_unique_legacy_node",
        name="Legacy unique",
    )

    migration_module._merge_evergo_features(django_apps, schema_editor=None)

    canonical.refresh_from_db()
    assert canonical.display == migration_module.CANONICAL_DISPLAY
    assert Feature.objects.filter(slug=migration_module.LEGACY_SLUG).count() == 0
    assert FeatureTest.objects.filter(feature=canonical, node_id=moved.node_id).count() == 1
    assert FeatureTest.objects.filter(feature=canonical, node_id="tests/test_sync.py::test_duplicate_node").count() == 1
