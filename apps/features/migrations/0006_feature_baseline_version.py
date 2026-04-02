from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.db import migrations, models
from packaging.version import InvalidVersion, Version

STATE_TABLE = "features_migration_0006_state"
STATE_KEY = "disabled_feature_ids"


def _ensure_state_table(schema_editor):
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
    _ensure_state_table(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])
        cursor.execute(
            f"INSERT INTO {STATE_TABLE} (state_key, payload) VALUES (%s, %s)",
            [STATE_KEY, json.dumps(payload, sort_keys=True)],
        )


def _load_state(schema_editor):
    _ensure_state_table(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT payload FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])
        row = cursor.fetchone()
    if row is None:
        return {}
    return json.loads(row[0])


def _clear_state(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])


def _parse_version(raw: str | None) -> Version | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text[1:] if text.lower().startswith("v") else text
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _current_suite_version() -> Version | None:
    version_path = Path(settings.BASE_DIR) / "VERSION"
    if not version_path.exists():
        return None
    return _parse_version(version_path.read_text(encoding="utf-8"))


def _disable_future_baseline_features(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    current_version = _current_suite_version()
    if current_version is None:
        _store_state(schema_editor, {"feature_ids": []})
        return

    features_to_disable_pks = []
    for feature in Feature.objects.filter(is_enabled=True).exclude(baseline_version=""):
        baseline = _parse_version(feature.baseline_version)
        if baseline is None:
            continue
        if current_version >= baseline:
            continue
        features_to_disable_pks.append(feature.pk)

    if features_to_disable_pks:
        Feature.objects.filter(pk__in=features_to_disable_pks).update(is_enabled=False)
    _store_state(schema_editor, {"feature_ids": features_to_disable_pks})


def _reenable_future_baseline_features(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    state = _load_state(schema_editor)
    features_to_enable_pks = state.get("feature_ids", [])

    if features_to_enable_pks:
        Feature.objects.filter(pk__in=features_to_enable_pks).update(is_enabled=True)
    _clear_state(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0005_merge_evergo_feature_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="feature",
            name="baseline_version",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional minimum Arthexis version where this suite feature should be enabled by default.",
                max_length=40,
            ),
        ),
        migrations.RunPython(_disable_future_baseline_features, _reenable_future_baseline_features),
    ]
