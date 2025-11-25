from django.db import migrations
from django.db.models import Q


def set_readme_as_default(apps, schema_editor):
    Module = apps.get_model("pages", "Module")

    Module.objects.filter(Q(path__iexact="/read/") | Q(path__iexact="/read")).update(
        is_default=True
    )


def unset_readme_as_default(apps, schema_editor):
    Module = apps.get_model("pages", "Module")

    Module.objects.filter(Q(path__iexact="/read/") | Q(path__iexact="/read")).update(
        is_default=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0045_ensure_module_priority_column"),
    ]

    operations = [
        migrations.RunPython(set_readme_as_default, unset_readme_as_default),
    ]
