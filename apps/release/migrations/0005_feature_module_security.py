from django.db import migrations


def restrict_feature_module_to_control_role(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    NodeRole = apps.get_model("nodes", "NodeRole")

    control_role, _ = NodeRole.objects.get_or_create(
        name="Control", defaults={"acronym": "CTRL"}
    )

    Module.objects.filter(path="/release/features/").update(
        security_mode="exclusive"
    )
    feature_module = Module.objects.filter(path="/release/features/").first()
    if feature_module:
        feature_module.roles.add(control_role)


class Migration(migrations.Migration):
    dependencies = [
        ("release", "0004_feature_module_link"),
        ("nodes", "0010_noderole_acronym"),
    ]

    operations = [
        migrations.RunPython(
            restrict_feature_module_to_control_role, migrations.RunPython.noop
        )
    ]
