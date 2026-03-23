"""Archive unsafe llm-summary command parameters and seed safe backend defaults."""

from __future__ import annotations

from django.db import migrations


FEATURE_SLUG = "llm-summary-suite"
ARCHIVE_KEY = "legacy_model_command_audit"
PARAMETERS_KEY = "parameters"


def _normalize_metadata(metadata):
    """Return a mutable metadata dictionary."""

    if isinstance(metadata, dict):
        return dict(metadata)
    return {}


def _normalize_parameters(metadata):
    """Return mutable feature parameter data from metadata."""

    parameters = metadata.get(PARAMETERS_KEY)
    if isinstance(parameters, dict):
        return dict(parameters)
    return {}


def archive_llm_summary_model_command(apps, schema_editor) -> None:
    """Move executable summary command text into non-executable feature metadata."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    feature = Feature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    metadata = _normalize_metadata(feature.metadata)
    parameters = _normalize_parameters(metadata)
    legacy_command = str(parameters.pop("model_command", "") or "").strip()
    parameters.pop("timeout_seconds", None)
    parameters["backend"] = str(parameters.get("backend") or "deterministic").strip() or "deterministic"
    metadata[PARAMETERS_KEY] = parameters
    if legacy_command:
        metadata[ARCHIVE_KEY] = legacy_command
    feature.metadata = metadata
    feature.save(update_fields=["metadata", "updated_at"])


def restore_llm_summary_model_command(apps, schema_editor) -> None:
    """Restore legacy summary command metadata during migration rollback."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    feature = Feature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    metadata = _normalize_metadata(feature.metadata)
    parameters = _normalize_parameters(metadata)
    parameters.pop("backend", None)
    parameters.setdefault("timeout_seconds", "240")
    archived_command = str(metadata.pop(ARCHIVE_KEY, "") or "").strip()
    parameters["model_command"] = archived_command
    metadata[PARAMETERS_KEY] = parameters
    feature.metadata = metadata
    feature.save(update_fields=["metadata", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0051_merge_20260321_2302"),
    ]

    operations = [
        migrations.RunPython(
            archive_llm_summary_model_command,
            restore_llm_summary_model_command,
        ),
    ]
