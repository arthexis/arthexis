import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("pages", "0001_initial"),
        ("sites", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="WhatsAppChatBridge",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        (
                            "is_enabled",
                            models.BooleanField(
                                default=True,
                                help_text="Disable to stop forwarding chat messages to this bridge.",
                            ),
                        ),
                        (
                            "is_default",
                            models.BooleanField(
                                default=False,
                                help_text="Use as the fallback bridge when no site-specific configuration is defined.",
                            ),
                        ),
                        (
                            "api_base_url",
                            models.URLField(
                                default="https://graph.facebook.com/v18.0",
                                help_text="Base URL for the Meta Graph API.",
                            ),
                        ),
                        (
                            "phone_number_id",
                            models.CharField(
                                help_text="Identifier of the WhatsApp phone number used for delivery.",
                                max_length=64,
                                verbose_name="Phone Number ID",
                            ),
                        ),
                        (
                            "access_token",
                            models.TextField(
                                help_text="Meta access token used to authenticate Graph API requests."
                            ),
                        ),
                        (
                            "site",
                            models.ForeignKey(
                                blank=True,
                                help_text="Restrict this bridge to a specific site. Leave blank to use it as a fallback.",
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="whatsapp_chat_bridges",
                                to="sites.site",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pages_whatsappchatbridge",
                        "ordering": ["site__domain", "pk"],
                        "verbose_name": "WhatsApp Chat Bridge",
                        "verbose_name_plural": "WhatsApp Chat Bridges",
                        "constraints": [
                            models.UniqueConstraint(
                                condition=models.Q(models.Q(("site__isnull", False)), _connector="AND"),
                                fields=("site",),
                                name="unique_whatsapp_chat_bridge_site",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(models.Q(("is_default", True)), _connector="AND"),
                                fields=("is_default",),
                                name="single_default_whatsapp_chat_bridge",
                            ),
                        ],
                    },
                ),
            ],
            database_operations=[],
        )
    ]
