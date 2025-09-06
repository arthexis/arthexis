import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from nodes.models import NodeRole


def test_update_fixtures_updates_changed_objects():
    role = NodeRole.objects.create(name="Original")
    fixture_dir = Path(settings.BASE_DIR) / "temp_app" / "fixtures"
    fixture_dir.mkdir(parents=True)
    fixture_path = fixture_dir / "node_roles.json"
    from django.core import serializers

    fixture_path.write_text(serializers.serialize("json", [role], indent=2))

    role.name = "Updated"
    role.save()

    call_command("update_fixtures")

    data = json.loads(fixture_path.read_text())
    assert data[0]["fields"]["name"] == "Updated"

    role.delete()
    shutil.rmtree(fixture_dir.parent)
