from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.prototypes.management.commands import prototype as prototype_command
from apps.prototypes.models import Prototype


def _configure_base(settings, base_dir: Path) -> None:
    settings.BASE_DIR = base_dir
    settings.APPS_DIR = base_dir / "apps"


@pytest.mark.django_db
def test_prototype_create_generates_record_and_hidden_scaffold(settings, tmp_path):
    _configure_base(settings, tmp_path)
    stdout = io.StringIO()

    call_command(
        "prototype",
        "create",
        "vision_lab",
        "--name",
        "Vision Lab",
        "--port",
        "8893",
        "--set",
        "DEBUG",
        "1",
        stdout=stdout,
    )

    prototype = Prototype.objects.get(slug="vision_lab")
    assert prototype.name == "Vision Lab"
    assert prototype.port == 8893
    assert prototype.env_overrides == {"DEBUG": "1"}
    assert prototype.app_module == "apps._prototypes.vision_lab"
    assert prototype.app_label == "prototype_vision_lab"
    assert prototype.sqlite_path == ""
    assert prototype.cache_dir == ""

    app_dir = tmp_path / "apps" / "_prototypes" / "vision_lab"
    assert (app_dir / "apps.py").exists()
    assert (app_dir / "models.py").exists()
    assert (app_dir / "routes.py").exists()

    output = stdout.getvalue()
    assert "Created prototype vision_lab" in output
    assert "python manage.py prototype activate vision_lab" in output
    assert "- sqlite: current" in output


@pytest.mark.django_db
def test_prototype_create_supports_existing_app_and_wslg_env(settings, tmp_path, monkeypatch):
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
    monkeypatch.setattr(prototype_command, "recommended_graphical_env", lambda: {
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": "/mnt/wslg/runtime-dir",
    })
    monkeypatch.syspath_prepend(str(tmp_path))

    stdout = io.StringIO()
    call_command(
        "prototype",
        "create",
        "pyxel_lab",
        "--app-module",
        "apps.pyxel",
        "--wslg-display",
        stdout=stdout,
    )

    prototype = Prototype.objects.get(slug="pyxel_lab")
    assert prototype.app_module == "apps.pyxel"
    assert prototype.app_label == "pyxel"
    assert prototype.env_overrides == {
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": "/mnt/wslg/runtime-dir",
    }
    assert prototype.sqlite_path == ""
    assert not (tmp_path / "apps" / "_prototypes" / "pyxel_lab").exists()
    assert "- mode: existing app" in stdout.getvalue()


@pytest.mark.django_db
def test_prototype_create_isolated_state_populates_default_paths(settings, tmp_path):
    _configure_base(settings, tmp_path)

    call_command("prototype", "create", "vision_db_lab", "--isolated-state")

    prototype = Prototype.objects.get(slug="vision_db_lab")
    assert prototype.sqlite_path == ".state/prototypes/vision_db_lab/db.sqlite3"
    assert prototype.sqlite_test_path == ".state/prototypes/vision_db_lab/test_db.sqlite3"
    assert prototype.cache_dir == ".state/prototypes/vision_db_lab/cache"


@pytest.mark.django_db
def test_prototype_activate_skips_restart_when_requested(settings, tmp_path, monkeypatch):
    _configure_base(settings, tmp_path)
    prototype = Prototype.objects.create(slug="audio_lab", name="Audio Lab", port=8896)
    restart_calls: list[dict[str, bool]] = []
    monkeypatch.setattr(
        prototype_command.prototype_ops,
        "restart_suite",
        lambda **kwargs: restart_calls.append(kwargs),
    )

    call_command("prototype", "activate", "audio_lab", "--no-restart")

    prototype.refresh_from_db()
    assert prototype.is_active is True
    assert restart_calls == []
    env_text = (tmp_path / "arthexis.env").read_text(encoding="utf-8")
    assert 'ARTHEXIS_ACTIVE_PROTOTYPE="audio_lab"' in env_text


@pytest.mark.django_db
def test_prototype_activate_restarts_by_default(settings, tmp_path, monkeypatch):
    _configure_base(settings, tmp_path)
    Prototype.objects.create(slug="maps_lab", name="Maps Lab", port=8897)
    restart_calls: list[dict[str, bool]] = []
    monkeypatch.setattr(
        prototype_command.prototype_ops,
        "restart_suite",
        lambda **kwargs: restart_calls.append(kwargs),
    )

    call_command("prototype", "activate", "maps_lab")

    assert restart_calls == [{"force_stop": False}]
