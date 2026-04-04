from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SampleConnector",
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
                ("title", models.CharField(max_length=128)),
            ],
            options={
                "verbose_name": "Sample connector",
                "verbose_name_plural": "Sample connectors",
            },
        ),
    ]
