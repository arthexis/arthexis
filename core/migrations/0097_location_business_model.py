from django.db import migrations, models


SQL_RENAME_TO_CORE = "ALTER TABLE ocpp_location RENAME TO core_location"
SQL_RENAME_TO_OCPP = "ALTER TABLE core_location RENAME TO ocpp_location"


def move_location_content_type(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    try:
        ct = ContentType.objects.get(app_label="ocpp", model="location")
    except ContentType.DoesNotExist:
        return
    ct.app_label = "core"
    ct.save(update_fields=["app_label"])


def revert_location_content_type(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    try:
        ct = ContentType.objects.get(app_label="core", model="location")
    except ContentType.DoesNotExist:
        return
    ct.app_label = "ocpp"
    ct.save(update_fields=["app_label"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0096_move_email_profiles"),
        ("ocpp", "0046_chargerlogrequest_last_status_payload_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(SQL_RENAME_TO_CORE, SQL_RENAME_TO_OCPP),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="Location",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("name", models.CharField(max_length=200)),
                        (
                            "latitude",
                            models.DecimalField(
                                blank=True, decimal_places=6, max_digits=9, null=True
                            ),
                        ),
                        (
                            "longitude",
                            models.DecimalField(
                                blank=True, decimal_places=6, max_digits=9, null=True
                            ),
                        ),
                        (
                            "zone",
                            models.CharField(
                                blank=True,
                                choices=[
                                    ("1", "Zone 1"),
                                    ("1A", "Zone 1A"),
                                    ("1B", "Zone 1B"),
                                    ("1C", "Zone 1C"),
                                    ("1D", "Zone 1D"),
                                    ("1E", "Zone 1E"),
                                    ("1F", "Zone 1F"),
                                ],
                                help_text="CFE climate zone used to select matching energy tariffs.",
                                max_length=3,
                                null=True,
                            ),
                        ),
                        (
                            "contract_type",
                            models.CharField(
                                blank=True,
                                choices=[
                                    ("domestic", "Domestic service (Tarifa 1)"),
                                    ("dac", "High consumption domestic (DAC)"),
                                    ("pdbt", "General service low demand (PDBT)"),
                                    ("gdbt", "General service high demand (GDBT)"),
                                    ("gdmto", "General distribution medium tension (GDMTO)"),
                                    ("gdmth", "General distribution medium tension hourly (GDMTH)"),
                                ],
                                help_text="CFE service contract type required to match energy tariff pricing.",
                                max_length=16,
                                null=True,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Location",
                        "verbose_name_plural": "Locations",
                    },
                )
            ],
        ),
        migrations.RunPython(
            move_location_content_type,
            revert_location_content_type,
        ),
    ]
