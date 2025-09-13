import json
from pathlib import Path
import tempfile

from django.core.management import call_command
from django.test import override_settings
from nodes.models import NodeRole


def test_update_fixtures_updates_changed_objects():
    role = NodeRole.objects.create(name="Original")
    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp)
        fixture_dir = base_dir / "temp_app" / "fixtures"
        fixture_dir.mkdir(parents=True)
        fixture_path = fixture_dir / "node_roles.json"
        from django.core import serializers

        fixture_path.write_text(serializers.serialize("json", [role], indent=2))

        role.name = "Updated"
        role.save()

        with override_settings(BASE_DIR=base_dir):
            call_command("update_fixtures")

        data = json.loads(fixture_path.read_text())
        assert data[0]["fields"]["name"] == "Updated"
    role.delete()
