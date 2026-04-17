from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("repos", "0002_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GitHubResponseTemplate",
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
                ("label", models.CharField(max_length=120)),
                ("body", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="github_response_templates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "GitHub Response Template",
                "verbose_name_plural": "GitHub Response Templates",
                "ordering": ("user__username", "label"),
            },
        ),
        migrations.AddConstraint(
            model_name="githubresponsetemplate",
            constraint=models.UniqueConstraint(
                fields=("user", "label"),
                name="unique_github_response_template_label_per_user",
            ),
        ),
    ]
