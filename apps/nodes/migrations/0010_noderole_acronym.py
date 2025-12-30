from django.db import migrations, models


ACRONYM_MAP = {
    "Terminal": "TERM",
    "Control": "CTRL",
    "Satellite": "SATL",
    "Watchtower": "WTTW",
    "Constellation": "CONS",
}


def assign_acronyms(apps, schema_editor):
    NodeRole = apps.get_model("nodes", "NodeRole")
    for name, acronym in ACRONYM_MAP.items():
        try:
            role = NodeRole.objects.get(name=name)
        except NodeRole.DoesNotExist:
            continue
        if role.acronym != acronym:
            role.acronym = acronym
            role.save(update_fields=["acronym"])


def remove_acronyms(apps, schema_editor):
    NodeRole = apps.get_model("nodes", "NodeRole")
    NodeRole.objects.all().update(acronym=None)


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0009_remove_arthexis_self_node"),
    ]

    operations = [
        migrations.AddField(
            model_name="noderole",
            name="acronym",
            field=models.CharField(max_length=4, unique=True, null=True, blank=True),
        ),
        migrations.RunPython(assign_acronyms, remove_acronyms),
    ]
