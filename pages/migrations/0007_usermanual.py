from __future__ import annotations

import json
from pathlib import Path

import json
from pathlib import Path

from django.db import migrations, models

from core.entity import Entity


def ensure_manual_table(apps, schema_editor):
    table_names = schema_editor.connection.introspection.table_names()
    if "man_usermanual" in table_names:
        return

    class Manual(models.Model):
        slug = models.SlugField(unique=True)
        title = models.CharField(max_length=200)
        description = models.CharField(max_length=200)
        languages = models.CharField(
            max_length=100,
            blank=True,
            default="",
            help_text="Comma-separated 2-letter language codes",
        )
        content_html = models.TextField()
        content_pdf = models.TextField()
        is_seed_data = models.BooleanField(default=False, editable=False)
        is_user_data = models.BooleanField(default=False, editable=False)
        is_deleted = models.BooleanField(default=False, editable=False)

        class Meta:
            app_label = "pages"
            db_table = "man_usermanual"
            managed = False

    schema_editor.create_model(Manual)


def migrate_manual_application(apps, schema_editor):
    Application = apps.get_model("pages", "Application")
    Module = apps.get_model("pages", "Module")

    pages_app, _ = Application.objects.get_or_create(
        name="pages", defaults={"description": ""}
    )

    try:
        manual_app = Application.objects.get(name="man")
    except Application.DoesNotExist:
        manual_app = None

    if manual_app:
        Module.objects.filter(application=manual_app).update(application=pages_app)
        manual_app.delete()


def update_manual_content_type(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="man", model="usermanual").update(
        app_label="pages"
    )


def load_seed_manuals(apps, schema_editor):
    UserManual = apps.get_model("pages", "UserManual")
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "manuals"
    fixture_files = [
        "manual__rpi-control.json",
        "manual__mcp-sigil-resolver.json",
    ]

    for fixture_name in fixture_files:
        fixture_path = fixtures_dir / fixture_name
        if not fixture_path.exists():
            continue
        data = json.loads(fixture_path.read_text())
        if not data:
            continue
        fields = data[0]["fields"]
        slug = fields["slug"]
        defaults = {
            "title": fields["title"],
            "description": fields["description"],
            "languages": fields.get("languages", ""),
            "content_html": fields["content_html"],
            "content_pdf": fields["content_pdf"],
            "is_seed_data": True,
            "is_user_data": False,
            "is_deleted": False,
        }
        UserManual.objects.update_or_create(slug=slug, defaults=defaults)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0006_merge_rfid_into_ocpp_module"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_manual_table, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="UserManual",
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
                        ("description", models.CharField(max_length=200)),
                        (
                            "languages",
                            models.CharField(
                                blank=True,
                                default="",
                                help_text="Comma-separated 2-letter language codes",
                                max_length=100,
                            ),
                        ),
                        ("content_html", models.TextField()),
                        (
                            "content_pdf",
                            models.TextField(help_text="Base64 encoded PDF"),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                    ],
                    options={
                        "db_table": "man_usermanual",
                        "verbose_name": "User Manual",
                        "verbose_name_plural": "User Manuals",
                    },
                    bases=(Entity,),
                )
            ],
        ),
        migrations.RunPython(migrate_manual_application, migrations.RunPython.noop),
        migrations.RunPython(update_manual_content_type, migrations.RunPython.noop),
        migrations.RunPython(load_seed_manuals, migrations.RunPython.noop),
    ]
