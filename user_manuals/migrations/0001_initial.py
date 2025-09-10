from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="UserManual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("title", models.CharField(max_length=200)),
                ("content_html", models.TextField()),
                ("content_pdf", models.TextField(help_text="Base64 encoded PDF")),
            ],
            options={
                "verbose_name": "User Manual",
                "verbose_name_plural": "User Manuals",
            },
        ),
    ]
