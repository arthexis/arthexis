from __future__ import annotations

import pytest
from django.test.utils import override_settings
from django.utils import timezone

from apps.prototypes import prototype_ops
from apps.prototypes.models import Prototype


def test_retirement_message_mentions_metadata_only_mode():
    assert "metadata only" in prototype_ops.RETIREMENT_MESSAGE


@pytest.mark.django_db
def test_retire_prototype_marks_record_inert():
    prototype = Prototype.objects.create(slug="vision_lab", name="Vision Lab")

    returned = prototype_ops.retire_prototype(prototype, note="Archived after workflow removal.")

    prototype.refresh_from_db()
    assert returned.pk == prototype.pk
    assert prototype.is_active is False
    assert prototype.is_runnable is False
    assert prototype.retired_at is not None
    assert prototype.retirement_notes == "Archived after workflow removal."


@pytest.mark.django_db
def test_prototype_clean_forces_non_runnable_state():
    prototype = Prototype(
        slug="audio_lab",
        name="Audio Lab",
        is_active=True,
        is_runnable=True,
        retired_at=timezone.now(),
    )

    prototype.full_clean()

    assert prototype.is_active is False
    assert prototype.is_runnable is False


@pytest.mark.django_db
def test_multiple_retired_rows_can_keep_blank_legacy_identifiers():
    Prototype.objects.create(slug="vision_lab", name="Vision Lab")
    Prototype.objects.create(slug="audio_lab", name="Audio Lab")

    assert Prototype.objects.filter(app_module="", app_label="").count() == 2


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp/arthexis-prototype-ops-test")
def test_clear_legacy_runtime_state_removes_overlay_files(tmp_path, settings):
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

    prototype_ops.clear_legacy_runtime_state(base_dir=tmp_path)

    assert env_file.read_text(encoding="utf-8") == "KEEP_ME=1\n"
    assert not (locks_dir / "active_prototype.lck").exists()
    assert (locks_dir / "backend_port.lck").read_text(encoding="utf-8") == "8000\n"
    assert not (locks_dir / "prototype_previous_backend_port.lck").exists()
