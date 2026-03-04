"""Add feature metadata and seed operator interface language parameter."""

from __future__ import annotations

from django.db import migrations, models


FEATURE_SLUG = "operator-site-interface"


def seed_operator_default_language(apps, schema_editor):
    """Ensure the operator site interface feature exposes a default language parameter."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    feature = Feature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    metadata = feature.metadata if isinstance(feature.metadata, dict) else {}
    parameters = metadata.get("parameters", {}) if isinstance(metadata, dict) else {}
    if not isinstance(parameters, dict):
        parameters = {}
    parameters.setdefault("default_language", "en")
    metadata["parameters"] = parameters
    feature.metadata = metadata
    feature.save(update_fields=["metadata", "updated_at"])


def unseed_operator_default_language(apps, schema_editor):
    """Remove the operator default language parameter from feature metadata."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    feature = Feature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    metadata = feature.metadata if isinstance(feature.metadata, dict) else {}
    parameters = metadata.get("parameters", {}) if isinstance(metadata, dict) else {}
    if not isinstance(parameters, dict):
        return

    if "default_language" not in parameters:
        return

    parameters.pop("default_language", None)
    if parameters:
        metadata["parameters"] = parameters
    else:
        metadata.pop("parameters", None)
    feature.metadata = metadata
    feature.save(update_fields=["metadata", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0029_update_ocpp_16j_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="feature",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Feature metadata including optional runtime parameters editable from admin.",
            ),
        ),
        migrations.RunPython(seed_operator_default_language, unseed_operator_default_language),
    ]
