import json
from pathlib import Path
from django.db import migrations


FIXTURE_NAME = "manual__ocpp-16.json"


def load_ocpp_manual(apps, schema_editor):
    UserManual = apps.get_model("pages", "UserManual")
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "manuals"
    fixture_path = fixtures_dir / FIXTURE_NAME
    if not fixture_path.exists():
        return
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not data:
        return
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
        ("pages", "0012_userstory_owner"),
    ]

    operations = [
        migrations.RunPython(load_ocpp_manual, migrations.RunPython.noop),
    ]
