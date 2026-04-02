from __future__ import annotations

import json

from django.db import migrations


CANONICAL_SLUG = "evergo-api-client"
LEGACY_SLUG = "evergo-integration"
CANONICAL_DISPLAY = "Evergo Integration"
CANONICAL_SUMMARY = (
    "Bind Suite users to Evergo credentials and synchronize profile, customer, "
    "and order metadata through Evergo endpoints."
)
CANONICAL_ADMIN_REQUIREMENTS = (
    "Provide admin model management for Evergo credentials with actions to "
    "validate authentication and load customer/order data."
)
CANONICAL_SERVICE_REQUIREMENTS = (
    "Provide a Django management command for CLI credential setup, login "
    "validation, and data synchronization."
)
CANONICAL_UPDATE_DATA = {
    "display": CANONICAL_DISPLAY,
    "summary": CANONICAL_SUMMARY,
    "admin_requirements": CANONICAL_ADMIN_REQUIREMENTS,
    "service_requirements": CANONICAL_SERVICE_REQUIREMENTS,
    "source": "mainstream",
}
CANONICAL_UPDATE_FIELDS = [*CANONICAL_UPDATE_DATA, "updated_at"]
STATE_TABLE = "features_migration_0005_state"
STATE_KEY = "evergo_merge_action"
ACTION_NO_RENAME = "no_rename"
ACTION_RENAMED_LEGACY = "renamed_legacy_to_canonical"


def _ensure_state_table(schema_editor):
    if schema_editor is None:
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                state_key VARCHAR(255) PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )


def _store_state(schema_editor, payload):
    if schema_editor is None:
        return

    _ensure_state_table(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])
        cursor.execute(
            f"INSERT INTO {STATE_TABLE} (state_key, payload) VALUES (%s, %s)",
            [STATE_KEY, json.dumps(payload, sort_keys=True)],
        )


def _load_state(schema_editor):
    if schema_editor is None:
        return {}

    _ensure_state_table(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT payload FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])
        row = cursor.fetchone()
    if row is None:
        return {}
    return json.loads(row[0])


def _clear_state(schema_editor):
    if schema_editor is None:
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])


def _apply_canonical_values(feature, *, include_slug: bool = False):
    for field, value in CANONICAL_UPDATE_DATA.items():
        setattr(feature, field, value)

    update_fields = ["slug", *CANONICAL_UPDATE_FIELDS] if include_slug else CANONICAL_UPDATE_FIELDS
    feature.save(update_fields=update_fields)


def _merge_evergo_features(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    FeatureNote = apps.get_model("features", "FeatureNote")
    FeatureTest = apps.get_model("features", "FeatureTest")

    canonical = Feature.objects.filter(slug=CANONICAL_SLUG).first()
    legacy = Feature.objects.filter(slug=LEGACY_SLUG).first()

    if canonical is None and legacy is None:
        _store_state(schema_editor, {"action": ACTION_NO_RENAME})
        return

    if canonical is None and legacy is not None:
        legacy.slug = CANONICAL_SLUG
        _apply_canonical_values(legacy, include_slug=True)
        _store_state(schema_editor, {"action": ACTION_RENAMED_LEGACY})
        return

    if legacy is not None:
        FeatureNote.objects.filter(feature_id=legacy.pk).update(feature_id=canonical.pk)
        canonical_node_ids = FeatureTest.objects.filter(feature_id=canonical.pk).values_list(
            "node_id", flat=True
        )
        FeatureTest.objects.filter(feature_id=legacy.pk, node_id__in=canonical_node_ids).delete()
        FeatureTest.objects.filter(feature_id=legacy.pk).update(feature_id=canonical.pk)
        Feature.objects.filter(pk=legacy.pk).delete()

    _apply_canonical_values(canonical)
    _store_state(schema_editor, {"action": ACTION_NO_RENAME})


def _restore_legacy_evergo_slug(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    state = _load_state(schema_editor)
    if state.get("action") != ACTION_RENAMED_LEGACY:
        _clear_state(schema_editor)
        return

    canonical = Feature.objects.filter(slug=CANONICAL_SLUG).first()
    legacy = Feature.objects.filter(slug=LEGACY_SLUG).first()

    if canonical is None or legacy is not None:
        _clear_state(schema_editor)
        return

    canonical.slug = LEGACY_SLUG
    canonical.save(update_fields=["slug", "updated_at"])
    _clear_state(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0004_initial"),
    ]

    operations = [
        migrations.RunPython(_merge_evergo_features, _restore_legacy_evergo_slug),
    ]
