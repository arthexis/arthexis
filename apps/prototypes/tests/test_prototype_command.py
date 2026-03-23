from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import override_settings
from django.utils import timezone

from apps.prototypes.models import Prototype
from apps.prototypes.prototype_ops import RETIREMENT_MESSAGE


@pytest.mark.django_db
def test_prototype_status_lists_retired_metadata_records():
    Prototype.objects.create(
        slug="vision_lab",
        name="Vision Lab",
        retired_at=timezone.now(),
        retirement_notes="Archived during scaffold retirement.",
    )
    stdout = io.StringIO()

    call_command("prototype", "status", stdout=stdout)

    output = stdout.getvalue()
    assert RETIREMENT_MESSAGE in output
    assert "vision_lab" in output
    assert "runnable=False" in output


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["activate", "create"])
def test_prototype_command_blocks_legacy_mutating_actions(action):
    with pytest.raises(CommandError, match="retired"):
        call_command("prototype", action)


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp/arthexis-prototype-command-test")
def test_prototype_deactivate_clears_legacy_runtime_state_without_restart(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    env_file = tmp_path / "arthexis.env"
    env_file.write_text(
        "KEEP_ME=1\n# BEGIN ARTHEXIS PROTOTYPE\nARTHEXIS_SQLITE_PATH=/tmp/prototype.sqlite3\n# END ARTHEXIS PROTOTYPE\n",
        encoding="utf-8",
    )
    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir(parents=True)
    (locks_dir / "active_prototype.lck").write_text("vision_lab\n", encoding="utf-8")
    (locks_dir / "backend_port.lck").write_text("8899\n", encoding="utf-8")
    (locks_dir / "prototype_previous_backend_port.lck").write_text("8000\n", encoding="utf-8")
    Prototype.objects.create(slug="vision_lab", name="Vision Lab", is_active=True)
    stdout = io.StringIO()

    call_command("prototype", "deactivate", "--no-restart", stdout=stdout)

    assert env_file.read_text(encoding="utf-8") == "KEEP_ME=1\n"
    assert not (locks_dir / "active_prototype.lck").exists()
    assert (locks_dir / "backend_port.lck").read_text(encoding="utf-8") == "8000\n"
    assert not (locks_dir / "prototype_previous_backend_port.lck").exists()
    assert "Cleared legacy prototype runtime state." in stdout.getvalue()
