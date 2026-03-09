from __future__ import annotations

from pathlib import Path

import pytest

from apps.prototypes import prototype_ops
from apps.prototypes.models import Prototype


def _configure_base(settings, base_dir: Path) -> None:
    settings.BASE_DIR = base_dir
    settings.APPS_DIR = base_dir / "apps"


@pytest.mark.django_db
def test_activate_prototype_writes_env_block_and_locks(settings, tmp_path):
    _configure_base(settings, tmp_path)
    prototype = Prototype.objects.create(
        slug="vision_lab",
        name="Vision Lab",
        port=8894,
        sqlite_path=".state/prototypes/vision_lab/db.sqlite3",
        sqlite_test_path=".state/prototypes/vision_lab/test_db.sqlite3",
        cache_dir=".state/prototypes/vision_lab/cache",
        env_overrides={"DEBUG": "1"},
    )

    prototype_ops.activate_prototype(prototype, base_dir=tmp_path)

    env_text = prototype_ops.env_path(tmp_path).read_text(encoding="utf-8")
    assert 'ARTHEXIS_ACTIVE_PROTOTYPE="vision_lab"' in env_text
    assert 'ARTHEXIS_PROTOTYPE_APP="apps._prototypes.vision_lab"' in env_text
    assert 'ARTHEXIS_SQLITE_PATH="' in env_text
    assert 'ARTHEXIS_SQLITE_TEST_PATH="' in env_text
    assert 'DJANGO_CACHE_DIR="' in env_text
    assert 'DEBUG="1"' in env_text

    assert (
        prototype_ops.active_prototype_lock_path(tmp_path).read_text(encoding="utf-8").strip()
        == "vision_lab"
    )
    assert (
        prototype_ops.backend_port_lock_path(tmp_path).read_text(encoding="utf-8").strip()
        == "8894"
    )

    prototype.refresh_from_db()
    assert prototype.is_active is True


@pytest.mark.django_db
def test_activate_prototype_omits_state_env_when_not_overridden(settings, tmp_path):
    _configure_base(settings, tmp_path)
    prototype = Prototype.objects.create(slug="maps_lab", name="Maps Lab", port=8898)

    prototype_ops.activate_prototype(prototype, base_dir=tmp_path)

    env_text = prototype_ops.env_path(tmp_path).read_text(encoding="utf-8")
    assert 'ARTHEXIS_ACTIVE_PROTOTYPE="maps_lab"' in env_text
    assert "ARTHEXIS_SQLITE_PATH" not in env_text
    assert "ARTHEXIS_SQLITE_TEST_PATH" not in env_text
    assert "DJANGO_CACHE_DIR" not in env_text


@pytest.mark.django_db
def test_deactivate_prototype_preserves_non_managed_env_content(settings, tmp_path):
    _configure_base(settings, tmp_path)
    env_path = prototype_ops.env_path(tmp_path)
    env_path.write_text('DEBUG="1"\n', encoding="utf-8")
    prototype = Prototype.objects.create(slug="chat_lab", name="Chat Lab", port=8895)

    prototype_ops.activate_prototype(prototype, base_dir=tmp_path)
    prototype_ops.deactivate_prototype(base_dir=tmp_path)

    assert env_path.read_text(encoding="utf-8") == 'DEBUG="1"\n'
    assert not prototype_ops.active_prototype_lock_path(tmp_path).exists()
    assert not prototype_ops.backend_port_lock_path(tmp_path).exists()
    assert Prototype.objects.filter(is_active=True).count() == 0


def test_scaffold_prototype_app_uses_existing_app_dir(settings, tmp_path, monkeypatch):
    _configure_base(settings, tmp_path)
    apps_root = tmp_path / "apps"
    pyxel_dir = apps_root / "pyxel"
    pyxel_dir.mkdir(parents=True, exist_ok=True)
    (apps_root / "__init__.py").write_text('"""test apps."""\n', encoding="utf-8")
    (pyxel_dir / "__init__.py").write_text('"""test pyxel app."""\n', encoding="utf-8")
    (pyxel_dir / "apps.py").write_text(
        "from django.apps import AppConfig\n\n\n"
        "class PyxelConfig(AppConfig):\n"
        '    default_auto_field = "django.db.models.BigAutoField"\n'
        '    name = "apps.pyxel"\n'
        '    label = "pyxel"\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    prototype = Prototype(slug="pyxel_lab", name="Pyxel Lab", app_module="apps.pyxel")

    app_dir = prototype_ops.scaffold_prototype_app(prototype, base_dir=tmp_path)

    assert app_dir == pyxel_dir
    assert not (tmp_path / "apps" / "_prototypes" / "pyxel_lab").exists()
