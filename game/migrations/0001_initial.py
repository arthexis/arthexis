from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GameMaterial",
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
                ("slug", models.SlugField(unique=True)),
                ("image", models.TextField()),
                ("description", models.TextField(blank=True)),
            ],
            options={"ordering": ["slug"]},
        ),
        migrations.CreateModel(
            name="GamePortal",
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
                ("slug", models.SlugField(unique=True)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                (
                    "entry_material",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        to="game.gamematerial",
                    ),
                ),
            ],
            options={
                "verbose_name": "Game",
                "verbose_name_plural": "Games",
                "ordering": ["title"],
            },
        ),
        migrations.CreateModel(
            name="MaterialRegion",
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
                ("name", models.CharField(max_length=200)),
                ("x", models.PositiveIntegerField()),
                ("y", models.PositiveIntegerField()),
                ("width", models.PositiveIntegerField()),
                ("height", models.PositiveIntegerField()),
                (
                    "material",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="regions",
                        to="game.gamematerial",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="incoming_regions",
                        to="game.gamematerial",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
    ]

