"""Provide User proxy for admin under auth app without integrator dependency."""

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProxy",
            fields=[],
            options={
                "proxy": True,
                "indexes": [],
                "verbose_name": "user",
                "verbose_name_plural": "users",
            },
            bases=(settings.AUTH_USER_MODEL,),
        ),
    ]
