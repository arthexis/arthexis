import apps.base.models
import django.contrib.auth.models
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0002_delete_dnsrecord"),
        ("users", "0001_initial"),
        ("core", "0007_move_user_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="User"),
                migrations.CreateModel(
                    name="User",
                    fields=[],
                    options={
                        "verbose_name": "User",
                        "verbose_name_plural": "Users",
                        "proxy": True,
                        "indexes": [],
                        "constraints": [],
                    },
                    bases=("users.user",),
                    managers=[
                        ("objects", apps.base.models.EntityUserManager()),
                        ("all_objects", django.contrib.auth.models.UserManager()),
                    ],
                ),
            ],
        )
    ]
