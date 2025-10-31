from django.db import migrations, models


def assign_node_origin(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    Charger = apps.get_model("ocpp", "Charger")

    local = None
    try:
        local = Node.get_local()
    except Exception:
        local = None

    for charger in Charger.objects.all():
        origin = charger.manager_node_id
        if not origin and local is not None:
            origin = local.pk
        if not origin:
            continue
        Charger.objects.filter(pk=charger.pk).update(node_origin_id=origin)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0033_location_contract_type_location_zone"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="allow_remote",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="charger",
            name="export_transactions",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="charger",
            name="last_online_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="charger",
            name="node_origin",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="origin_chargers",
                to="nodes.node",
            ),
        ),
        migrations.RunPython(assign_node_origin, reverse_code=noop),
    ]
