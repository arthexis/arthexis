from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("locals", "0002_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HiddenAdminApp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("app_label", models.CharField(max_length=100)),
                (
                    "user",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="hidden_admin_apps", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "verbose_name": "Hidden admin app",
                "verbose_name_plural": "Hidden admin apps",
                "db_table": "locals_hidden_admin_app",
                "ordering": ["app_label", "pk"],
                "unique_together": {("user", "app_label")},
            },
        ),
    ]
