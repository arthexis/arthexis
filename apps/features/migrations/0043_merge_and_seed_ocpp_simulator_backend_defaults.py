"""Merge conflicting feature leaves and seed OCPP simulator backend defaults."""

from django.db import migrations


OCPP_SIMULATOR_FEATURE_SLUG = "ocpp-simulator"
SIMULATOR_PARAMETERS_KEY = "parameters"
MOBILITY_HOUSE_BACKEND_PARAMETER_KEY = "mobilityhouse_backend"
ARTHEXIS_BACKEND_PARAMETER_KEY = "arthexis_backend"
MIGRATION_MARKER_KEY = "_seeded_by_0043_ocpp_backend_defaults"


def seed_ocpp_simulator_backend_defaults(apps, schema_editor):
    """Ensure OCPP simulator parameters include enabled backend defaults when missing."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    feature = Feature.objects.filter(slug=OCPP_SIMULATOR_FEATURE_SLUG).first()
    if feature is None:
        return

    metadata = feature.metadata if isinstance(feature.metadata, dict) else {}
    parameters = metadata.get(SIMULATOR_PARAMETERS_KEY)
    if not isinstance(parameters, dict):
        parameters = {}

    seeded_keys = []
    if feature.is_enabled and MOBILITY_HOUSE_BACKEND_PARAMETER_KEY not in parameters:
        parameters[MOBILITY_HOUSE_BACKEND_PARAMETER_KEY] = "enabled"
        seeded_keys.append(MOBILITY_HOUSE_BACKEND_PARAMETER_KEY)
    if ARTHEXIS_BACKEND_PARAMETER_KEY not in parameters:
        parameters[ARTHEXIS_BACKEND_PARAMETER_KEY] = "enabled"
        seeded_keys.append(ARTHEXIS_BACKEND_PARAMETER_KEY)

    if not seeded_keys:
        return

    metadata[SIMULATOR_PARAMETERS_KEY] = parameters
    metadata[MIGRATION_MARKER_KEY] = seeded_keys
    feature.metadata = metadata
    feature.save(update_fields=["metadata"])


def unseed_ocpp_simulator_backend_defaults(apps, schema_editor):
    """Undo seeded simulator defaults when values still match this migration output."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    feature = Feature.objects.filter(slug=OCPP_SIMULATOR_FEATURE_SLUG).first()
    if feature is None:
        return

    metadata = feature.metadata if isinstance(feature.metadata, dict) else {}
    parameters = metadata.get(SIMULATOR_PARAMETERS_KEY)
    if not isinstance(parameters, dict):
        return

    seeded_keys = metadata.get(MIGRATION_MARKER_KEY)
    if not isinstance(seeded_keys, list):
        return

    changed = False
    for key in seeded_keys:
        if key in {MOBILITY_HOUSE_BACKEND_PARAMETER_KEY, ARTHEXIS_BACKEND_PARAMETER_KEY} and parameters.get(key) == "enabled":
            parameters.pop(key, None)
            changed = True

    metadata.pop(MIGRATION_MARKER_KEY, None)

    if not changed:
        if parameters:
            metadata[SIMULATOR_PARAMETERS_KEY] = parameters
        else:
            metadata.pop(SIMULATOR_PARAMETERS_KEY, None)
        feature.metadata = metadata
        feature.save(update_fields=["metadata"])
        return

    if parameters:
        metadata[SIMULATOR_PARAMETERS_KEY] = parameters
    else:
        metadata.pop(SIMULATOR_PARAMETERS_KEY, None)

    feature.metadata = metadata
    feature.save(update_fields=["metadata"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0041_merge_20260309_0000"),
        ("features", "0042_seed_release_management_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_ocpp_simulator_backend_defaults,
            unseed_ocpp_simulator_backend_defaults,
        ),
    ]
