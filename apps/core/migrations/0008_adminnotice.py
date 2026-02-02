from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_upgrade_policy_notice(apps, schema_editor):
    AdminNotice = apps.get_model("core", "AdminNotice")
    AdminNotice.objects.create(
        message=(
            "Upgrade policies now control auto-upgrade behavior. "
            "Review the new Stable, Unstable, Fast Lane, and LTS policies "
            "and update node roles as needed."
        )
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_usageevent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminNotice",
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
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dismissed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "dismissed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dismissed_admin_notices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Admin Notice",
                "verbose_name_plural": "Admin Notices",
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(
            create_upgrade_policy_notice, reverse_code=migrations.RunPython.noop
        ),
    ]
