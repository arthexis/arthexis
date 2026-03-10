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


@pytest.mark.django_db
def test_scaffold_prototype_app_escapes_embedded_python_literals(settings, tmp_path):
    _configure_base(settings, tmp_path)
    prototype = Prototype.objects.create(
        slug="escape_lab",
        name='My Prototype" \\nimport os; os.system("id") #',
        app_module='apps._prototypes.escape_lab"\nmalicious = True\n"',
        app_label='prototype_escape_lab"\nmalicious = True\n"',
        port=8898,
    )

    app_dir = prototype_ops.scaffold_prototype_app(prototype, base_dir=tmp_path)
    apps_source = (app_dir / "apps.py").read_text(encoding="utf-8")
    models_source = (app_dir / "models.py").read_text(encoding="utf-8")

    assert 'name = \'apps._prototypes.escape_lab"\\nmalicious = True\\n"\'' in apps_source
    assert 'label = \'prototype_escape_lab"\\nmalicious = True\\n"\'' in apps_source
    assert (
        'verbose_name = \'My Prototype" \\\\nimport os; os.system("id") #\'' in apps_source
    )
    assert "verbose_name = 'My Prototype\" \\\\nimport os; os.system(\"id\") # record'" in models_source
    assert "verbose_name_plural = 'My Prototype\" \\\\nimport os; os.system(\"id\") # records'" in models_source


def test_restart_suite_strips_managed_prototype_env(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    def _fake_run(command, cwd, check, env):
        calls.append({"command": command, "cwd": cwd, "check": check, "env": env})
        return None

    monkeypatch.setattr(prototype_ops.subprocess, "run", _fake_run)
    monkeypatch.setenv("ARTHEXIS_ACTIVE_PROTOTYPE", "vision_lab")
    monkeypatch.setenv("ARTHEXIS_PROTOTYPE_APP", "apps._prototypes.vision_lab")
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", "/tmp/prototype.sqlite3")
    monkeypatch.setenv("DJANGO_CACHE_DIR", "/tmp/prototype-cache")
    monkeypatch.setenv("KEEP_ME", "1")

    prototype_ops.restart_suite(base_dir=tmp_path)

    assert [call["command"] for call in calls] == [["./stop.sh"], ["./start.sh", "--await"]]
    for call in calls:
        env = call["env"]
        assert isinstance(env, dict)
        for managed_key in Prototype.RESERVED_ENV_KEYS:
            assert managed_key not in env
        assert env.get("KEEP_ME") == "1"
