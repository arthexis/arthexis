from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0024_ocpp_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompatibilityEvidence",
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
                ("charger_identity", models.CharField(blank=True, max_length=255)),
                ("kind", models.CharField(max_length=80)),
                ("protocol", models.CharField(blank=True, max_length=32)),
                ("unique_id", models.CharField(blank=True, max_length=255)),
                ("action", models.CharField(blank=True, max_length=120)),
                ("details", models.JSONField(default=dict)),
                ("observed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "charger",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compatibility_evidence",
                        to="ocpp.charger",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["charger", "kind", "observed_at"],
                        name="ocpp_compat_charger_kind_idx",
                    ),
                    models.Index(
                        fields=["charger_identity", "kind", "observed_at"],
                        name="ocpp_compat_identity_kind_idx",
                    ),
                ],
            },
        ),
    ]
