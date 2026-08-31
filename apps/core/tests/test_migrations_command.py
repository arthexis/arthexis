"""Smoke regression tests for the unified migrations management command."""

from __future__ import annotations

import io
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands.migrations import Command


def _disable_benchmark_preflight(monkeypatch):
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.Command._benchmark_preflight",
        staticmethod(lambda **_: None),
    )


def _seed_apps_root(base_dir):
    apps_dir = base_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "__init__.py").write_text(
        '"""Project application packages."""\n', encoding="utf-8"
    )
    return apps_dir


def _seed_app_migrations(apps_dir, app_label):
    migrations_dir = apps_dir / app_label / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "__init__.py").write_text("", encoding="utf-8")
    migration_path = migrations_dir / "0001_initial.py"
    migration_path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        "    operations = [\n"
        "    ]\n",
        encoding="utf-8",
    )
    return migration_path


def test_migrations_pending_reports_clean_state(monkeypatch):
    """migrations pending should fail closed when no pending work exists."""

    class _FakeGraph:
        def leaf_nodes(self):
            return []

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": _FakeGraph(),
                    "migrated_apps": {"catalog", "core"},
                },
            )()

        def migration_plan(self, targets):
            assert targets == []
            return []

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": object()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    with pytest.raises(CommandError, match="no pending migrations"):
        call_command("migrations", "pending")


def test_migrations_rebuild_regenerates_initial_migration(
    monkeypatch, settings, tmp_path
):
    """migrations rebuild should clear stale files and regenerate migrations."""

    apps_dir = _seed_apps_root(tmp_path)
    settings.BASE_DIR = tmp_path
    settings.APPS_DIR = apps_dir
    _seed_app_migrations(apps_dir, "catalog")

    stale = apps_dir / "catalog" / "migrations" / "0002_stale.py"
    stale.write_text("# stale\n", encoding="utf-8")
    invoked_labels = []

    def _fake_call_command(name, *args, **kwargs):
        if name != "makemigrations":
            raise AssertionError(f"Unexpected command: {name} {args}")
        invoked_labels.extend(args)
        _seed_app_migrations(apps_dir, "catalog")

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command", _fake_call_command
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.Command._get_project_app_labels",
        lambda self, _apps_dir: ["catalog"],
    )

    call_command("migrations", "rebuild")

    content = (apps_dir / "catalog" / "migrations" / "0001_initial.py").read_text(
        encoding="utf-8"
    )
    assert not stale.exists()
    assert "BranchTagOperation" not in content
    assert invoked_labels == ["catalog"]


def test_migrations_benchmark_reports_plan_without_applying(monkeypatch, tmp_path):
    """migrations benchmark should default to plan-only JSON output."""
    _disable_benchmark_preflight(monkeypatch)

    class _FakeMigration:
        app_label = "core"
        name = "0001_initial"

    class _FakeGraph:
        def leaf_nodes(self, app_label=None):
            assert app_label is None
            return [("core", "0001_initial")]

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": _FakeGraph(),
                    "migrated_apps": {"catalog", "core"},
                },
            )()

        def migration_plan(self, targets):
            assert targets == [("core", "0001_initial")]
            return [(_FakeMigration(), False)]

    output_path = tmp_path / "migration-benchmark.json"

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": type("Connection", (), {"vendor": "sqlite"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    def _unexpected_apply(name, *args, **kwargs):
        raise AssertionError(
            f"Unexpected migration application: {name} {args} {kwargs}"
        )

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command",
        _unexpected_apply,
    )

    stdout = io.StringIO()
    call_command(
        "migrations",
        "benchmark",
        "--output",
        str(output_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload == json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["database"] == {"alias": "default", "vendor": "sqlite"}
    assert payload["planning"]["pending_count"] == 1
    assert payload["planning"]["migrations"] == [
        {
            "app_label": "core",
            "migration_name": "0001_initial",
            "backwards": False,
        }
    ]
    assert payload["execution"]["mode"] == "plan-only"
    assert payload["execution"]["applied"] is False




def test_migrations_impact_emits_json(monkeypatch, settings, tmp_path):
    """migrations impact should emit structured JSON for changed migrations."""

    settings.BASE_DIR = tmp_path
    payload = {
        "base_ref": "origin/main",
        "git": {"base": "base", "head": "head"},
        "migration_files": [],
        "operation_classes": [],
        "risk": {"level": "low", "reasons": []},
        "schema_version": 1,
        "static_lint": [],
        "summary": {
            "changed_migration_count": 0,
            "data_migration_count": 0,
            "fixture_or_seed_files": [],
        },
        "tool": "migration-impact",
    }

    def _fake_impact(repo_root, *, base_ref):
        assert repo_root == tmp_path
        assert base_ref == "origin/main"
        return payload

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.build_migration_impact_report",
        _fake_impact,
    )

    stdout = io.StringIO()
    call_command("migrations", "impact", "--format", "json", stdout=stdout)

    assert json.loads(stdout.getvalue())["tool"] == "migration-impact"


def test_migrations_impact_writes_markdown(monkeypatch, settings, tmp_path):
    """migrations impact should write optional markdown output."""

    settings.BASE_DIR = tmp_path
    payload = {
        "base_ref": "origin/main",
        "git": {"base": "base", "head": "head"},
        "migration_files": [],
        "operation_classes": [],
        "risk": {"level": "low", "reasons": []},
        "schema_version": 1,
        "static_lint": [],
        "summary": {
            "changed_migration_count": 0,
            "data_migration_count": 0,
            "fixture_or_seed_files": [],
        },
        "tool": "migration-impact",
    }

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.build_migration_impact_report",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.format_migration_impact_markdown",
        lambda _payload: "# Migration Impact Report\n",
    )

    stdout = io.StringIO()
    call_command(
        "migrations",
        "impact",
        "--output",
        "work/migration-impact.md",
        stdout=stdout,
    )

    output_path = tmp_path / "work" / "migration-impact.md"
    assert output_path.read_text(encoding="utf-8") == "# Migration Impact Report\n"
    assert stdout.getvalue() == "# Migration Impact Report\n"


def test_migrations_benchmark_rejects_unknown_app_label(monkeypatch):
    """migrations benchmark should fail when an app label is unknown."""
    _disable_benchmark_preflight(monkeypatch)

    class _FakeGraph:
        def leaf_nodes(self, app_label=None):
            raise AssertionError(f"Unexpected leaf lookup for {app_label}")

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": _FakeGraph(),
                    "migrated_apps": {"core", "catalog"},
                    "replacements": {},
                },
            )()

        def migration_plan(self, targets):
            raise AssertionError(f"Unexpected plan request for {targets}")

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": type("Connection", (), {"vendor": "sqlite"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    with pytest.raises(CommandError, match="Unknown app label: typo_app"):
        call_command("migrations", "benchmark", "typo_app")


def test_migrations_benchmark_rejects_unknown_migration_target(monkeypatch):
    """migrations benchmark should fail when an explicit target is unknown."""
    _disable_benchmark_preflight(monkeypatch)

    class _FakeGraph:
        nodes = {("core", "0001_initial")}

        def leaf_nodes(self, app_label=None):
            raise AssertionError(f"Unexpected leaf lookup for {app_label}")

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self._migrations = {"core": {"0001_initial": object()}}
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": _FakeGraph(),
                    "migrated_apps": {"core", "catalog"},
                    "replacements": {},
                    "get_migration_by_prefix": self._get_migration_by_prefix,
                },
            )()

        def _get_migration_by_prefix(self, app_label, migration_name):
            app_migrations = self._migrations.get(app_label, {})
            matches = [
                name for name in app_migrations if name.startswith(migration_name)
            ]
            if not matches:
                raise KeyError(migration_name)
            return type("Migration", (), {"name": matches[0]})()

        def migration_plan(self, targets):
            raise AssertionError(f"Unexpected plan request for {targets}")

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": type("Connection", (), {"vendor": "sqlite"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    with pytest.raises(
        CommandError,
        match="Unknown migration target: catalog 0002_missing",
    ):
        call_command("migrations", "benchmark", "catalog", "0002_missing")


def test_migrations_benchmark_apply_uses_requested_database(monkeypatch, tmp_path):
    """migrations benchmark --apply should time an explicit migrate call."""
    _disable_benchmark_preflight(monkeypatch)

    class _FakeMigration:
        app_label = "catalog"
        name = "0002_add_fields"

    class _FakeGraph:
        nodes = {("catalog", "0002_add_fields")}

        def leaf_nodes(self, app_label=None):
            raise AssertionError(f"Unexpected leaf lookup for {app_label}")

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self._migrations = {"catalog": {"0002_add_fields": object()}}
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": _FakeGraph(),
                    "migrated_apps": {"catalog", "core"},
                    "replacements": {},
                    "get_migration_by_prefix": self._get_migration_by_prefix,
                },
            )()

        def _get_migration_by_prefix(self, app_label, migration_name):
            app_migrations = self._migrations.get(app_label, {})
            matches = [
                name for name in app_migrations if name.startswith(migration_name)
            ]
            if not matches:
                raise KeyError(migration_name)
            return type("Migration", (), {"name": matches[0]})()

        def migration_plan(self, targets):
            assert targets == [("catalog", "0002_add_fields")]
            return [(_FakeMigration(), False)]

    applied = []
    output_path = tmp_path / "migration-benchmark.json"

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"reporting": type("Connection", (), {"vendor": "postgresql"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    def _fake_call_command(name, *args, **kwargs):
        applied.append((name, args, kwargs))

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.call_command",
        _fake_call_command,
    )

    stdout = io.StringIO()
    call_command(
        "migrations",
        "benchmark",
        "catalog",
        "0002_add_fields",
        "--apply",
        "--database",
        "reporting",
        "--output",
        str(output_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["database"] == {"alias": "reporting", "vendor": "postgresql"}
    assert payload["target"]["targets"] == [
        {"app_label": "catalog", "migration_name": "0002_add_fields"}
    ]
    assert payload["execution"]["mode"] == "apply"
    assert payload["execution"]["applied"] is True
    assert applied == [
        (
            "migrate",
            ("catalog", "0002_add_fields"),
            {"database": "reporting", "interactive": False, "verbosity": 0},
        )
    ]


def test_migrations_benchmark_allows_replaced_migration_target(monkeypatch):
    """migrations benchmark should accept valid replacement-pruned targets."""
    _disable_benchmark_preflight(monkeypatch)

    class _FakeMigration:
        app_label = "catalog"
        name = "0002_squashed"

    class _FakeGraph:
        nodes = {("catalog", "0002_squashed")}

        def leaf_nodes(self, app_label=None):
            raise AssertionError(f"Unexpected leaf lookup for {app_label}")

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self._migrations = {"catalog": {"0001_initial": object()}}
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": _FakeGraph(),
                    "migrated_apps": {"catalog", "core"},
                    "replacements": {
                        ("catalog", "0001_initial"): type(
                            "Replacement",
                            (),
                            {"replaces": [("catalog", "0001_initial")]},
                        )()
                    },
                    "get_migration_by_prefix": self._get_migration_by_prefix,
                },
            )()

        def migration_plan(self, targets):
            assert targets == [("catalog", "0001_initial")]
            return [(_FakeMigration(), False)]

        def _get_migration_by_prefix(self, app_label, migration_name):
            app_migrations = self._migrations.get(app_label, {})
            matches = [
                name for name in app_migrations if name.startswith(migration_name)
            ]
            if not matches:
                raise KeyError(migration_name)
            return type("Migration", (), {"name": matches[0]})()

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": type("Connection", (), {"vendor": "sqlite"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    call_command("migrations", "benchmark", "catalog", "0001_initial")


def test_migrations_benchmark_maps_zero_target(monkeypatch):
    """migrations benchmark should support explicit zero targets."""
    _disable_benchmark_preflight(monkeypatch)

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type(
                "Loader",
                (),
                {
                    "graph": type("Graph", (), {"nodes": set(), "leaf_nodes": lambda *_: []})(),
                    "migrated_apps": {"catalog"},
                    "replacements": {},
                },
            )()

        def migration_plan(self, targets):
            assert targets == [("catalog", None)]
            return []

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": type("Connection", (), {"vendor": "sqlite"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    call_command("migrations", "benchmark", "catalog", "zero")


def test_migrations_benchmark_fails_on_conflicts(monkeypatch):
    """migrations benchmark should surface migration conflicts like migrate."""

    class _FakeExecutor:
        def __init__(self, connection):
            self.connection = connection
            self.loader = type(
                "Loader",
                (),
                {
                    "check_consistent_history": lambda *_: None,
                    "detect_conflicts": lambda *_: {"catalog": ["0002_a", "0002_b"]},
                },
            )()

    monkeypatch.setattr(
        "apps.core.management.commands.migrations.connections",
        {"default": type("Connection", (), {"vendor": "sqlite"})()},
    )
    monkeypatch.setattr(
        "apps.core.management.commands.migrations.MigrationExecutor",
        _FakeExecutor,
    )

    with pytest.raises(CommandError, match="Conflicting migrations detected"):
        call_command("migrations", "benchmark")


def test_migrations_benchmark_output_path_uses_subsecond_precision():
    """default benchmark output path should include subsecond precision."""

    output_path = Command._benchmark_output_path(output=None, run_id=None)
    assert output_path.name.startswith("migration-benchmark-")
    assert output_path.name.endswith("Z.json")
    timestamp = output_path.name.removeprefix("migration-benchmark-").removesuffix(
        ".json"
    )
    assert len(timestamp) == len("20260101T000000000000Z")


def test_migrations_benchmark_rejects_path_traversal_run_id():
    """default benchmark output path should reject traversal via --run-id."""

    for run_id in ("x/../../pwn", "foo/", ".."):
        with pytest.raises(
            CommandError,
            match=(
                "Invalid run ID: path traversal or subdirectories are not allowed "
                "in --run-id."
            ),
        ):
            Command._benchmark_output_path(output=None, run_id=run_id)
