from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models
import core.entity
import django.contrib.auth.models


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
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("host", models.CharField(max_length=100)),
                ("port", models.PositiveIntegerField(default=587)),
                ("username", models.CharField(blank=True, max_length=100)),
                ("password", models.CharField(blank=True, max_length=100)),
                ("use_tls", models.BooleanField(default=True)),
                ("use_ssl", models.BooleanField(default=False)),
                ("from_email", models.EmailField(blank=True, max_length=254)),
                (
                    "node",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_outbox",
                        to="nodes.node",
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

