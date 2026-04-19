"""Tests for env-refresh migration mismatch fallback behavior."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from django.core.management.base import CommandError

from utils.migration_branches import BranchTagConflictError


@pytest.fixture
def env_refresh_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    module_path = Path(__file__).resolve().parents[1] / "env-refresh.py"
    spec = importlib.util.spec_from_file_location("env_refresh_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load env-refresh module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(
            BASE_DIR=tmp_path,
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(tmp_path / "db.sqlite3"),
                }
            },
        ),
    )
    monkeypatch.setattr(module, "connection", SimpleNamespace(in_atomic_block=False))
    monkeypatch.setattr(module, "call_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_local_app_labels", lambda: ["core"])
    monkeypatch.setattr(module, "_migration_hash", lambda apps: "hash")
    monkeypatch.setattr(module, "_pending_migration_graph", lambda: True)
    monkeypatch.setattr(module, "_remove_integrator_from_auth_migration", lambda: None)
    monkeypatch.setattr(module, "_unlink_sqlite_db", lambda path: None)

    return module


def test_branch_tag_conflict_auto_reconcile_fallback(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    migrate_calls: list[dict[str, object]] = []
    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "migrations.md5").write_text("old-hash")

    def fake_run_migrate(*, using_sqlite: bool, default_db: dict[str, str], interactive: bool) -> None:
        migrate_calls.append(
            {
                "using_sqlite": using_sqlite,
                "default_db": default_db,
                "interactive": interactive,
            }
        )
        raise BranchTagConflictError(
            "rebuild-2026",
            "core.0001_initial",
            conflicts=["core.0001_previous"],
        )

    monkeypatch.setattr(env_refresh_module, "_run_migrate", fake_run_migrate)
    monkeypatch.setattr(
        env_refresh_module,
        "_prepare_reconcile_snapshot",
        lambda **kwargs: (tmp_path / "snapshot.sqlite3", tmp_path / "db.sqlite3", None),
    )

    with pytest.raises(CommandError):
        env_refresh_module.run_database_tasks(
            auto_reconcile_on_mismatch=True,
            force_db=True,
        )

    output = capsys.readouterr().out
    assert "branch tag conflict" in output
    assert "Auto-reconcile fallback engaged" in output
    assert len(migrate_calls) == 2


def test_branch_tag_conflict_without_reconcile_fails_fast(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    migrate_calls = 0

    def fake_run_migrate(*, using_sqlite: bool, default_db: dict[str, str], interactive: bool) -> None:
        nonlocal migrate_calls
        migrate_calls += 1
        raise BranchTagConflictError(
            "rebuild-2026",
            "core.0001_initial",
            conflicts=["core.0001_previous"],
        )

    monkeypatch.setattr(env_refresh_module, "_run_migrate", fake_run_migrate)

    with pytest.raises(CommandError):
        env_refresh_module.run_database_tasks(force_db=True)

    output = capsys.readouterr().out
    assert "branch tag conflict" in output
    assert "Auto-reconcile fallback engaged" not in output
    assert migrate_calls == 1


def test_resolve_content_type_natural_key_returns_none_when_missing(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingManager:
        def get_by_natural_key(self, app_label: str, model_name: str):
            raise env_refresh_module.ContentType.DoesNotExist

    monkeypatch.setattr(env_refresh_module.ContentType, "objects", MissingManager())

    assert (
        env_refresh_module._resolve_content_type_natural_key(["gallery", "galleryimage"])
        is None
    )


def test_resolve_content_type_natural_key_returns_content_type_when_present(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()

    class PresentManager:
        def get_by_natural_key(self, app_label: str, model_name: str):
            assert (app_label, model_name) == ("gallery", "galleryimage")
            return sentinel

    monkeypatch.setattr(env_refresh_module.ContentType, "objects", PresentManager())

    assert (
        env_refresh_module._resolve_content_type_natural_key(
            {"app_label": "gallery", "model": "galleryimage"}
        )
        is sentinel
    )
