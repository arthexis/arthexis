from django.db import migrations


def forward_update_acronyms(apps, schema_editor):
    NodeRole = apps.get_model("nodes", "NodeRole")
    NodeRole.objects.filter(name="Terminal", acronym="TERM").update(acronym="TRMN")
    NodeRole.objects.filter(name="Satellite", acronym="SATL").update(acronym="STLT")


def reverse_update_acronyms(apps, schema_editor):
    NodeRole = apps.get_model("nodes", "NodeRole")
    NodeRole.objects.filter(name="Terminal", acronym="TRMN").update(acronym="TERM")
    NodeRole.objects.filter(name="Satellite", acronym="STLT").update(acronym="SATL")


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0041_set_default_upgrade_policy_channels"),
    ]

    operations = [
        migrations.RunPython(forward_update_acronyms, reverse_update_acronyms),
    ]
