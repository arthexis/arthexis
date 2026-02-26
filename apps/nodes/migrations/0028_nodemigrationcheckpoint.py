from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0027_update_ap_router_roles"),
    ]

    operations = [
        migrations.CreateModel(
            name="NodeMigrationCheckpoint",
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
                ("key", models.CharField(max_length=120, unique=True)),
                ("processed_items", models.PositiveIntegerField(default=0)),
                ("total_items", models.PositiveIntegerField(default=0)),
                ("last_pk", models.BigIntegerField(blank=True, null=True)),
                ("is_complete", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Node migration checkpoint",
                "verbose_name_plural": "Node migration checkpoints",
                "ordering": ("key",),
            },
        ),
    ]
