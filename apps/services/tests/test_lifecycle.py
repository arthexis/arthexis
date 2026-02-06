import json
from pathlib import Path

import pytest

from apps.services.lifecycle import (
    SYSTEMD_UNITS_LOCK,
    LIFECYCLE_CONFIG,
    build_lifecycle_service_units,
    write_lifecycle_config,
)

@pytest.mark.django_db
def test_build_lifecycle_service_units_respects_lockfiles(tmp_path: Path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "service.lck").write_text("demo", encoding="utf-8")
    (lock_dir / "celery.lck").write_text("", encoding="utf-8")

    units = build_lifecycle_service_units(tmp_path)
    celery_worker = next(
        unit for unit in units if unit["unit_display"] == "celery-demo.service"
    )

    assert celery_worker["configured"] is True


@pytest.mark.django_db
def test_write_lifecycle_config_generates_lock_files(tmp_path: Path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "service.lck").write_text("demo", encoding="utf-8")
    (lock_dir / "rfid-service.lck").write_text("", encoding="utf-8")

    config = write_lifecycle_config(tmp_path)

    config_path = lock_dir / LIFECYCLE_CONFIG
    systemd_path = lock_dir / SYSTEMD_UNITS_LOCK
    assert config_path.exists()
    assert systemd_path.exists()

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["service_name"] == "demo"

    assert "rfid-demo.service" in payload["systemd_units"]
    assert "rfid-demo.service" in systemd_path.read_text(encoding="utf-8")
