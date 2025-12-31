from django.db import migrations


def create_feature_module(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    NodeRole = apps.get_model("nodes", "NodeRole")

    module, _created = Module.objects.get_or_create(
        path="/release/features/",
        defaults={
            "menu": "Release",
            "priority": 20,
            "is_default": False,
        },
    )
    control_role = NodeRole.objects.filter(name__iexact="Control").first()
    if control_role:
        module.roles.add(control_role)


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0003_feature_featureartifact_featuretestcase_and_more"),
        ("modules", "0003_normalize_module_paths"),
        ("nodes", "0010_noderole_acronym"),
    ]

    operations = [migrations.RunPython(create_feature_module, migrations.RunPython.noop)]
