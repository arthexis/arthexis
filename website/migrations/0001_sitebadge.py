from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteBadge",
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
                (
                    "badge_color",
                    models.CharField(default="#28a745", max_length=7),
                ),
                (
                    "site",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="badge",
                        to="sites.site",
                    ),
                ),
            ],
        ),
    ]
