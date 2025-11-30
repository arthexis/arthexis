import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0001_initial"),
        ("sites", "0001_initial"),
        ("odoo", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="OdooChatBridge",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("is_enabled", models.BooleanField(default=True, help_text="Disable to stop forwarding chat messages to this bridge.")),
                        ("is_default", models.BooleanField(default=False, help_text="Use as the fallback bridge when no site-specific configuration is defined.")),
                        ("channel_id", models.PositiveIntegerField(help_text="Identifier of the Odoo mail.channel that should receive forwarded messages.", verbose_name="Channel ID")),
                        ("channel_uuid", models.CharField(blank=True, help_text="Optional UUID of the Odoo mail.channel for reference.", max_length=64, verbose_name="Channel UUID")),
                        ("notify_partner_ids", models.JSONField(blank=True, default=list, help_text="Additional Odoo partner IDs to notify when posting messages. Provide a JSON array of integers.")),
                        ("profile", models.ForeignKey(help_text="Verified Odoo employee credentials used to post chat messages.", on_delete=django.db.models.deletion.CASCADE, related_name="chat_bridges", to="odoo.odooprofile")),
                        ("site", models.ForeignKey(blank=True, help_text="Restrict this bridge to a specific site. Leave blank to use it as a fallback.", null=True, on_delete=django.db.models.deletion.CASCADE, related_name="odoo_chat_bridges", to="sites.site")),
                    ],
                    options={
                        "db_table": "pages_odoochatbridge",
                        "ordering": ["site__domain", "pk"],
                        "verbose_name": "Odoo Chat Bridge",
                        "verbose_name_plural": "Odoo Chat Bridges",
                    },
                ),
                migrations.AddConstraint(
                    model_name="odoochatbridge",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(models.Q(("site__isnull", False)), _connector="AND"),
                        fields=("site",),
                        name="unique_odoo_chat_bridge_site",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="odoochatbridge",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(models.Q(("is_default", True)), _connector="AND"),
                        fields=("is_default",),
                        name="single_default_odoo_chat_bridge",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
