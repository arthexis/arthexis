from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models
import core.entity
import django.contrib.auth.models
import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="contentsample",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="EmailOutbox",
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
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                (
                    "host",
                    core.fields.SigilShortAutoField(
                        max_length=100,
                        help_text="Gmail: smtp.gmail.com. GoDaddy: smtpout.secureserver.net",
                    ),
                ),
                (
                    "port",
                    models.PositiveIntegerField(
                        default=587,
                        help_text="Gmail: 587 (TLS). GoDaddy: 587 (TLS) or 465 (SSL)",
                    ),
                ),
                (
                    "username",
                    core.fields.SigilShortAutoField(
                        blank=True,
                        max_length=100,
                        help_text="Full email address for Gmail or GoDaddy",
                    ),
                ),
                (
                    "password",
                    core.fields.SigilShortAutoField(
                        blank=True,
                        max_length=100,
                        help_text="Email account password or app password",
                    ),
                ),
                (
                    "use_tls",
                    models.BooleanField(
                        default=True,
                        help_text="Check for Gmail or GoDaddy on port 587",
                    ),
                ),
                (
                    "use_ssl",
                    models.BooleanField(
                        default=False,
                        help_text="Check for GoDaddy on port 465; Gmail does not use SSL",
                    ),
                ),
                (
                    "from_email",
                    core.fields.SigilShortAutoField(
                        blank=True,
                        max_length=254,
                        verbose_name="From Email",
                        help_text="Default From address; usually the same as username",
                    ),
                ),
            ],
            options={
                "verbose_name": "Email Outbox",
                "verbose_name_plural": "Email Outboxes",
            },
            bases=(core.entity.Entity,),
        ),
        migrations.CreateModel(
            name="User",
            fields=[],
            options={
                "verbose_name": "user",
                "verbose_name_plural": "users",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.user",),
            managers=[
                ("objects", core.entity.EntityUserManager()),
                ("all_objects", django.contrib.auth.models.UserManager()),
            ],
        ),
    ]
