from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


def load_ocpp_manual(apps, schema_editor):
    UserManual = apps.get_model("pages", "UserManual")
    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "manuals"
        / "manual__ocpp-1-6.json"
    )
    if not fixture_path.exists():
        return

    data = json.loads(fixture_path.read_text())
    if not data:
        return

    fields = data[0]["fields"]
    defaults = {
        "title": fields["title"],
        "description": fields["description"],
        "languages": fields.get("languages", ""),
        "content_html": fields["content_html"],
        "content_pdf": fields["content_pdf"],
        "is_seed_data": fields.get("is_seed_data", False),
        "is_user_data": fields.get("is_user_data", False),
        "is_deleted": fields.get("is_deleted", False),
    }
    UserManual.objects.update_or_create(slug=fields["slug"], defaults=defaults)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0011_userstory_github_issue_number_and_more"),
    ]

    operations = [
        migrations.RunPython(load_ocpp_manual, migrations.RunPython.noop),
    ]
