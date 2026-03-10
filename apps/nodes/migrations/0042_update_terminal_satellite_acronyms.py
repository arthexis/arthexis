from django.db import migrations


ACRONYM_UPDATES = {
    "Terminal": ("TERM", "TRMN"),
    "Satellite": ("SATL", "STLT"),
}


def forward_update_acronyms(apps, schema_editor):
    NodeRole = apps.get_model("nodes", "NodeRole")
    for name, (old_acronym, new_acronym) in ACRONYM_UPDATES.items():
        NodeRole.objects.filter(name=name, acronym=old_acronym).update(acronym=new_acronym)


def reverse_update_acronyms(apps, schema_editor):
    NodeRole = apps.get_model("nodes", "NodeRole")
    for name, (old_acronym, new_acronym) in ACRONYM_UPDATES.items():
        NodeRole.objects.filter(name=name, acronym=new_acronym).update(acronym=old_acronym)


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0041_set_default_upgrade_policy_channels"),
    ]

    operations = [
        migrations.RunPython(forward_update_acronyms, reverse_update_acronyms),
    ]
