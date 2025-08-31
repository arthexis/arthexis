from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GamePortal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("play_url", models.CharField(blank=True, max_length=200)),
                ("download_url", models.CharField(blank=True, max_length=200)),
            ],
            options={
                "verbose_name": "Game",
                "verbose_name_plural": "Games",
                "ordering": ["title"],
            },
        ),
    ]
