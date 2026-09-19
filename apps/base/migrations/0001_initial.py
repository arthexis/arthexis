from django.db import migrations, models


def record_generation(apps, schema_editor):
    schema_generation = apps.get_model("base", "SchemaGeneration")
    schema_generation.objects.update_or_create(generation=2)


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SchemaGeneration",
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
                ("generation", models.PositiveSmallIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.RunPython(record_generation, migrations.RunPython.noop),
    ]
