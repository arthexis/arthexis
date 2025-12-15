from django.db import migrations


def remove_seed_nodes(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    Node.objects.filter(is_seed_data=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0004_remove_invalid_clipboard_tasks"),
    ]

    operations = [
        migrations.RunPython(remove_seed_nodes, reverse_code=migrations.RunPython.noop),
    ]
