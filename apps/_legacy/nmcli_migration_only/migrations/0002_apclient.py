from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nmcli", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="APClient",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "connection_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("interface_name", models.CharField(blank=True, default="", max_length=100)),
                ("mac_address", models.CharField(db_index=True, max_length=64)),
                ("signal_dbm", models.IntegerField(blank=True, null=True)),
                ("rx_bitrate_mbps", models.FloatField(blank=True, null=True)),
                ("tx_bitrate_mbps", models.FloatField(blank=True, null=True)),
                ("inactive_time_ms", models.IntegerField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "AP Client",
                "verbose_name_plural": "AP Clients",
                "ordering": ("-last_seen_at", "mac_address"),
                "unique_together": {("mac_address", "interface_name")},
            },
        ),
    ]
