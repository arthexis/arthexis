from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0011_merge_20260103_1323"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceInventorySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("request_id", models.BigIntegerField(blank=True, null=True, verbose_name="Request ID")),
                ("seq_no", models.BigIntegerField(blank=True, null=True, verbose_name="Sequence Number")),
                ("generated_at", models.DateTimeField(blank=True, null=True, verbose_name="Generated At")),
                ("tbc", models.BooleanField(default=False, verbose_name="To Be Continued")),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("reported_at", models.DateTimeField(auto_now_add=True, verbose_name="Reported at")),
                ("charger", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="device_inventory_snapshots", to="ocpp.charger")),
            ],
            options={
                "verbose_name": "Device Inventory Snapshot",
                "verbose_name_plural": "Device Inventory Snapshots",
                "ordering": ["-reported_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="DeviceInventoryItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("component_name", models.CharField(max_length=200, verbose_name="Component")),
                ("component_instance", models.CharField(blank=True, default="", max_length=200, verbose_name="Component Instance")),
                ("variable_name", models.CharField(max_length=200, verbose_name="Variable")),
                ("variable_instance", models.CharField(blank=True, default="", max_length=200, verbose_name="Variable Instance")),
                ("attributes", models.JSONField(blank=True, default=list)),
                ("characteristics", models.JSONField(blank=True, default=dict)),
                ("reported_at", models.DateTimeField(auto_now_add=True, verbose_name="Reported at")),
                ("snapshot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="ocpp.deviceinventorysnapshot")),
            ],
            options={
                "verbose_name": "Device Inventory Item",
                "verbose_name_plural": "Device Inventory Items",
                "ordering": ["snapshot", "component_name", "variable_name", "id"],
            },
        ),
    ]

