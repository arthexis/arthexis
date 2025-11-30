from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("pages", "0005_move_favorite_to_locals"),
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Favorite",
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
                        ("custom_label", models.CharField(blank=True, max_length=100)),
                        ("user_data", models.BooleanField(default=False)),
                        ("priority", models.IntegerField(default=0)),
                        (
                            "content_type",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                to="contenttypes.contenttype",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="favorites",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pages_favorite",
                        "verbose_name": "Favorite",
                        "verbose_name_plural": "Favorites",
                        "ordering": ["priority", "pk"],
                        "unique_together": {("user", "content_type")},
                    },
                ),
            ],
            database_operations=[],
        )
    ]
