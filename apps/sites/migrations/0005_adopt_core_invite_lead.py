import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


MODEL_NAME = "invitelead"


def _move_content_type(apps, source_label: str, target_label: str) -> None:
    ContentType = apps.get_model("contenttypes", "ContentType")
    source = ContentType.objects.filter(
        app_label=source_label,
        model=MODEL_NAME,
    ).first()
    if source is None:
        return

    target = ContentType.objects.filter(
        app_label=target_label,
        model=MODEL_NAME,
    ).first()
    if target is not None and target.pk != source.pk:
        raise RuntimeError(
            "Cannot transfer InviteLead ContentType ownership because both "
            f"{source_label}.{MODEL_NAME} and {target_label}.{MODEL_NAME} exist."
        )

    source.app_label = target_label
    source.save(update_fields=["app_label"])


def move_core_content_type_to_pages(apps, schema_editor):
    del schema_editor
    _move_content_type(apps, "core", "pages")


def move_pages_content_type_back_to_core(apps, schema_editor):
    del schema_editor
    _move_content_type(apps, "pages", "core")


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0004_release_remaining_models"),
        ("emails", "0004_adopt_core_email_models"),
        ("pages", "0004_remove_workgroup_play_panel_item"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    move_core_content_type_to_pages,
                    move_pages_content_type_back_to_core,
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="InviteLead",
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
                        (
                            "user",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        ("path", models.TextField(blank=True)),
                        ("referer", models.TextField(blank=True)),
                        ("user_agent", models.TextField(blank=True)),
                        (
                            "ip_address",
                            models.CharField(
                                blank=True,
                                max_length=45,
                                validators=[django.core.validators.validate_ipv46_address],
                            ),
                        ),
                        ("created_on", models.DateTimeField(auto_now_add=True)),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("open", "Open"),
                                    ("assigned", "Assigned"),
                                    ("closed", "Closed"),
                                    ("spam", "Spam"),
                                ],
                                default="open",
                                max_length=20,
                            ),
                        ),
                        (
                            "assign_to",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="core_invitelead_assignments",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        ("email", models.EmailField(max_length=254)),
                        ("comment", models.TextField(blank=True)),
                        ("sent_on", models.DateTimeField(blank=True, null=True)),
                        ("error", models.TextField(blank=True)),
                        ("mac_address", models.CharField(blank=True, max_length=17)),
                        (
                            "sent_via_outbox",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="invite_leads",
                                to="emails.emailoutbox",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "core_invitelead",
                        "verbose_name": "Invite Lead",
                        "verbose_name_plural": "Invite Leads",
                    },
                ),
            ],
        ),
    ]
