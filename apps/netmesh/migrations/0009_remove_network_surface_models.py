from django.db import migrations


def _remove_deleted_model_contenttypes(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    stale_model_names = [
        "nodeendpoint",
        "noderelayconfig",
        "relayregion",
        "serviceadvertisement",
    ]
    content_types = ContentType.objects.filter(app_label="netmesh", model__in=stale_model_names)
    Permission.objects.filter(content_type__in=content_types).delete()
    content_types.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("netmesh", "0008_remove_nodekeymaterial_netmesh_node_single_active_key_and_more"),
    ]

    operations = [
        migrations.DeleteModel(
            name="NodeEndpoint",
        ),
        migrations.DeleteModel(
            name="NodeRelayConfig",
        ),
        migrations.DeleteModel(
            name="RelayRegion",
        ),
        migrations.DeleteModel(
            name="ServiceAdvertisement",
        ),
        migrations.RunPython(_remove_deleted_model_contenttypes, migrations.RunPython.noop),
    ]
