"""Merge conflicting feature leaves and seed OCPP simulator backend defaults."""

from django.db import migrations


OCPP_SIMULATOR_FEATURE_SLUG = "ocpp-simulator"
SIMULATOR_PARAMETERS_KEY = "parameters"
MOBILITY_HOUSE_BACKEND_PARAMETER_KEY = "mobilityhouse_backend"
ARTHEXIS_BACKEND_PARAMETER_KEY = "arthexis_backend"


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

    changed = False
    if feature.is_enabled and MOBILITY_HOUSE_BACKEND_PARAMETER_KEY not in parameters:
        parameters[MOBILITY_HOUSE_BACKEND_PARAMETER_KEY] = "enabled"
        changed = True
    if ARTHEXIS_BACKEND_PARAMETER_KEY not in parameters:
        parameters[ARTHEXIS_BACKEND_PARAMETER_KEY] = "enabled"
        changed = True

    if not changed:
        return

    metadata[SIMULATOR_PARAMETERS_KEY] = parameters
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

    changed = False
    if parameters.get(MOBILITY_HOUSE_BACKEND_PARAMETER_KEY) == "enabled":
        parameters.pop(MOBILITY_HOUSE_BACKEND_PARAMETER_KEY, None)
        changed = True
    if parameters.get(ARTHEXIS_BACKEND_PARAMETER_KEY) == "enabled":
        parameters.pop(ARTHEXIS_BACKEND_PARAMETER_KEY, None)
        changed = True

    if not changed:
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
