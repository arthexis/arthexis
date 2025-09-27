from django.db import migrations


def remove_postgres_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="postgres-db").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0011_alter_netmessage_reach"),
    ]

    operations = [
        migrations.RunPython(remove_postgres_feature, migrations.RunPython.noop),
    ]
