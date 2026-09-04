"""Tests for env-refresh migration mismatch fallback behavior."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from django.core.management.base import CommandError

from tests.gate_markers import gate
from utils.migration_branches import BranchTagConflictError

pytestmark = [gate.upgrade]


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
    monkeypatch.setattr(
        module,
        "connection",
        SimpleNamespace(alias="default", in_atomic_block=False),
    )
    monkeypatch.setattr(module, "call_command", lambda *args, **kwargs: None)
    module._real_local_app_labels = module._local_app_labels
    monkeypatch.setattr(module, "_local_app_labels", lambda: ["core"])
    monkeypatch.setattr(module, "_migration_hash", lambda apps: "hash")
    monkeypatch.setattr(module, "_pending_migration_graph", lambda: True)
    monkeypatch.setattr(module, "_remove_integrator_from_auth_migration", lambda: None)
    module._real_record_fully_applied_replacement_migrations = (
        module._record_fully_applied_replacement_migrations
    )
    monkeypatch.setattr(
        module,
        "_record_fully_applied_replacement_migrations",
        lambda: 0,
    )
    monkeypatch.setattr(module, "_unlink_sqlite_db", lambda path: None)
    monkeypatch.setattr(
        module,
        "_run_manage_makemigrations",
        lambda *args: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

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

    def fake_run_migrate(
        *, using_sqlite: bool, default_db: dict[str, str], interactive: bool
    ) -> None:
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


def test_auto_reconcile_rebuilds_sqlite_after_history_check_failure(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.sqlite3"
    database = tmp_path / "db.sqlite3"
    database.write_text("stale ledger")
    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "migrations.md5").write_text("old-hash")
    fresh_database = False
    migrations_ran: list[dict[str, object]] = []

    def unlink_database(path: Path) -> None:
        nonlocal fresh_database
        assert path == database
        fresh_database = True

    def fake_makemigrations(*args: str) -> SimpleNamespace:
        if "--check" in args and not fresh_database:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="InconsistentMigrationHistory: stale squash ledger",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(env_refresh_module, "_unlink_sqlite_db", unlink_database)
    monkeypatch.setattr(env_refresh_module, "_run_manage_makemigrations", fake_makemigrations)
    monkeypatch.setattr(
        env_refresh_module,
        "_prepare_reconcile_snapshot",
        lambda **kwargs: (snapshot, database, None),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_run_migrate",
        lambda **kwargs: migrations_ran.append(kwargs),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "reconcile_sqlite_tables",
        lambda **kwargs: SimpleNamespace(
            copied_tables=[],
            skipped_tables=[],
            skipped_columns={},
            skipped_rows={},
            missing_in_target=[],
            missing_in_source=[],
            backend="sqlite",
        ),
    )
    monkeypatch.setattr(env_refresh_module, "_ensure_content_types", lambda **kwargs: None)
    monkeypatch.setattr(env_refresh_module, "_fixture_files", lambda: [])
    monkeypatch.setattr(
        env_refresh_module,
        "connection",
        SimpleNamespace(
            alias="default",
            in_atomic_block=False,
            introspection=SimpleNamespace(table_names=lambda: []),
        ),
    )
    monkeypatch.setattr(env_refresh_module, "load_local_seed_zips", lambda: 0)
    monkeypatch.setattr(
        env_refresh_module,
        "get_user_model",
        lambda: SimpleNamespace(objects=SimpleNamespace(all=lambda: [])),
    )
    monkeypatch.setattr(env_refresh_module, "generate_model_sigils", lambda: None)
    monkeypatch.setattr(
        env_refresh_module,
        "load_shared_user_fixtures",
        lambda force=True: None,
    )
    monkeypatch.setattr(
        env_refresh_module.Node,
        "register_current",
        lambda notify_peers=False: (SimpleNamespace(public_endpoint="test.local"), False),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_call_command_with_sqlite_lock_retry",
        lambda *args, **kwargs: None,
    )

    env_refresh_module.run_database_tasks(
        auto_reconcile_on_mismatch=True,
        force_db=True,
    )

    assert fresh_database
    assert len(migrations_ran) == 1
    assert "Auto-reconcile fallback engaged" in capsys.readouterr().out


def test_auto_reconcile_leaves_postgres_history_failures_for_explicit_repair(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_refresh_module.settings.DATABASES["default"]["ENGINE"] = (
        "django.db.backends.postgresql"
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_prepare_reconcile_snapshot",
        lambda **kwargs: (tmp_path / "snapshot.sqlite3", None, "arthexis_reconcile"),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_check_or_write_migrations",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            env_refresh_module.InconsistentMigrationHistory("stale ledger")
        ),
    )

    with pytest.raises(env_refresh_module.InconsistentMigrationHistory):
        env_refresh_module.run_database_tasks(auto_reconcile_on_mismatch=True)


def test_env_refresh_checks_migrations_without_writing_by_default(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_makemigrations(*args: str) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        env_refresh_module,
        "_run_manage_makemigrations",
        fake_makemigrations,
    )

    env_refresh_module._check_or_write_migrations(
        ["nodes"],
        using_sqlite=True,
        default_db={"NAME": str(tmp_path / "db.sqlite3")},
        write_migrations=False,
    )

    assert calls == [
        ("nodes", "--check", "--dry-run", "--noinput"),
    ]


def test_sqlite_reconciliation_validates_fresh_migration_state(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    migration_checks: list[object] = []
    migrations_ran: list[object] = []
    unlinked_databases: list[Path] = []

    monkeypatch.setattr(
        env_refresh_module,
        "_check_or_write_migrations",
        lambda *args, **kwargs: migration_checks.append((args, kwargs)),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_prepare_reconcile_snapshot",
        lambda **kwargs: (tmp_path / "snapshot.sqlite3", tmp_path / "db.sqlite3", None),
    )
    monkeypatch.setattr(env_refresh_module, "_schema_needs_migration", lambda: True)
    monkeypatch.setattr(
        env_refresh_module,
        "_unlink_sqlite_db",
        lambda path: unlinked_databases.append(path),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_run_migrate",
        lambda **kwargs: migrations_ran.append(kwargs),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "reconcile_sqlite_tables",
        lambda **kwargs: SimpleNamespace(
            copied_tables=[],
            skipped_tables=[],
            skipped_columns={},
            skipped_rows={},
            missing_in_target=[],
            missing_in_source=[],
            backend="sqlite",
        ),
    )
    monkeypatch.setattr(env_refresh_module, "_ensure_content_types", lambda **kwargs: None)
    monkeypatch.setattr(env_refresh_module, "_fixture_files", lambda: [])
    monkeypatch.setattr(
        env_refresh_module,
        "get_user_model",
        lambda: SimpleNamespace(objects=SimpleNamespace(all=lambda: [])),
    )
    monkeypatch.setattr(env_refresh_module, "generate_model_sigils", lambda: None)
    monkeypatch.setattr(
        env_refresh_module, "load_shared_user_fixtures", lambda force=True: None
    )
    monkeypatch.setattr(env_refresh_module, "load_local_seed_zips", lambda: 0)
    monkeypatch.setattr(
        env_refresh_module.Node,
        "register_current",
        lambda notify_peers=False: (SimpleNamespace(public_endpoint="test.local"), False),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_call_command_with_sqlite_lock_retry",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        env_refresh_module,
        "connection",
        SimpleNamespace(
            alias="default",
            in_atomic_block=False,
            introspection=SimpleNamespace(table_names=lambda: []),
        ),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "MigrationRecorder",
        lambda _connection: (_ for _ in ()).throw(
            env_refresh_module.OperationalError("fresh database")
        ),
    )

    env_refresh_module.run_database_tasks(
        migrate_reconcile=True,
        write_migrations=True,
    )

    assert len(migration_checks) == 1
    assert migration_checks[0][1]["write_migrations"] is True
    assert len(migrations_ran) == 1
    assert unlinked_databases == [tmp_path / "db.sqlite3"]
    assert "Skipping old-database migration validation" not in capsys.readouterr().out


def test_sqlite_reconciliation_reuses_preserved_snapshot(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    database_path = tmp_path / "db.sqlite3"
    database_path.write_text("partial target")
    snapshot_path = locks_dir / "db.pre_major_migrate.sqlite3"
    snapshot_path.write_text("complete source")
    backup_calls: list[object] = []

    monkeypatch.setattr(
        env_refresh_module,
        "backup_sqlite_database",
        lambda *args: backup_calls.append(args),
    )

    backup, database, postgres_name = env_refresh_module._prepare_reconcile_snapshot(
        using_sqlite=True,
        default_db={"NAME": str(database_path)},
        locks_dir=locks_dir,
        base_dir=tmp_path,
    )

    assert backup == snapshot_path
    assert database == database_path
    assert postgres_name is None
    assert backup.read_text() == "complete source"
    assert backup_calls == []
    assert "Reusing preserved pre-migration backup" in capsys.readouterr().out


def test_sqlite_reconciliation_reuses_snapshot_without_target_database(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    database_path = tmp_path / "db.sqlite3"
    snapshot_path = locks_dir / "db.pre_major_migrate.sqlite3"
    snapshot_path.write_text("complete source")

    monkeypatch.setattr(
        env_refresh_module,
        "backup_sqlite_database",
        lambda *args: pytest.fail("must not replace the preserved snapshot"),
    )

    backup, database, postgres_name = env_refresh_module._prepare_reconcile_snapshot(
        using_sqlite=True,
        default_db={"NAME": str(database_path)},
        locks_dir=locks_dir,
        base_dir=tmp_path,
    )

    assert backup == snapshot_path
    assert database == database_path
    assert postgres_name is None
    assert backup.read_text() == "complete source"


def test_successful_auto_reconcile_attempt_removes_unused_snapshot(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / ".locks" / "db.pre_major_migrate.sqlite3"
    snapshot_path.parent.mkdir()
    snapshot_path.write_text("current source")

    monkeypatch.setattr(
        env_refresh_module,
        "_prepare_reconcile_snapshot",
        lambda **kwargs: (snapshot_path, tmp_path / "db.sqlite3", None),
    )
    monkeypatch.setattr(env_refresh_module, "_pending_migration_graph", lambda: False)
    monkeypatch.setattr(env_refresh_module, "_fixture_files", lambda: [])
    monkeypatch.setattr(env_refresh_module, "load_local_seed_zips", lambda: 0)
    monkeypatch.setattr(
        env_refresh_module.Node,
        "register_current",
        lambda notify_peers=False: (SimpleNamespace(public_endpoint="test.local"), False),
    )

    env_refresh_module.run_database_tasks(auto_reconcile_on_mismatch=True)

    assert not snapshot_path.exists()


@pytest.mark.django_db
def test_dashboard_rule_fixture_upsert_reuses_restored_content_type(
    env_refresh_module: ModuleType,
) -> None:
    from django.contrib.contenttypes.models import ContentType

    from apps.counters.models import DashboardRule
    from apps.nodes.models import Node

    content_type = ContentType.objects.get_for_model(Node)
    DashboardRule.objects.create(
        name="Old node rule",
        content_type=content_type,
        function_name="old_rule",
    )

    assert env_refresh_module._upsert_dashboard_rule(
        DashboardRule,
        {
            "name": "Node Health",
            "content_type": ["nodes", "node"],
            "implementation": "python",
            "function_name": "evaluate_node_rules",
            "success_message": "All rules met.",
            "failure_message": "",
        },
    )

    assert DashboardRule.objects.filter(content_type=content_type).count() == 1
    rule = DashboardRule.objects.get(content_type=content_type)
    assert rule.name == "Node Health"
    assert rule.function_name == "evaluate_node_rules"


@pytest.mark.django_db
def test_dashboard_rule_fixture_upsert_reconciles_name_and_content_type(
    env_refresh_module: ModuleType,
) -> None:
    from django.contrib.contenttypes.models import ContentType

    from apps.counters.models import DashboardRule
    from apps.nodes.models import Node
    from apps.sites.models import SiteConfiguration

    node_content_type = ContentType.objects.get_for_model(Node)
    site_content_type = ContentType.objects.get_for_model(SiteConfiguration)
    DashboardRule.objects.create(
        name="Node Health",
        content_type=site_content_type,
        function_name="old_rule",
    )
    DashboardRule.objects.create(
        name="Old node rule",
        content_type=node_content_type,
        function_name="other_rule",
    )

    assert env_refresh_module._upsert_dashboard_rule(
        DashboardRule,
        {
            "name": "Node Health",
            "content_type": ["nodes", "node"],
            "implementation": "python",
            "function_name": "evaluate_node_rules",
            "success_message": "All rules met.",
            "failure_message": "",
        },
    )

    assert DashboardRule.objects.count() == 1
    rule = DashboardRule.objects.get(name="Node Health")
    assert rule.content_type == node_content_type
    assert rule.function_name == "evaluate_node_rules"


def test_run_migrate_uses_fake_initial_by_default(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_call_command(*args: str, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(env_refresh_module, "call_command", fake_call_command)

    env_refresh_module._run_migrate(
        using_sqlite=False,
        default_db={},
        interactive=False,
    )

    assert calls == [
        (("migrate",), {"interactive": False, "fake_initial": True}),
    ]


def test_record_fully_applied_replacement_migrations(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement_key = ("cards", "0003_initial")
    replaced_keys = {
        ("cards", "0003_cardface"),
        ("cards", "0004_offeringsoul"),
    }
    recorded: list[tuple[str, str]] = []

    class FakeLoader:
        def __init__(self, connection: object, ignore_no_migrations: bool) -> None:
            assert connection is env_refresh_module.connection
            assert ignore_no_migrations is True
            self.replacements = {
                replacement_key: SimpleNamespace(replaces=sorted(replaced_keys))
            }

    class FakeRecorder:
        def __init__(self, connection: object) -> None:
            assert connection is env_refresh_module.connection

        def applied_migrations(self) -> set[tuple[str, str]]:
            return set(replaced_keys)

        def record_applied(self, app: str, name: str) -> None:
            recorded.append((app, name))

    monkeypatch.setattr(env_refresh_module, "MigrationLoader", FakeLoader)
    monkeypatch.setattr(env_refresh_module, "MigrationRecorder", FakeRecorder)

    assert env_refresh_module._real_record_fully_applied_replacement_migrations() == 1
    assert recorded == [replacement_key]


def test_record_fully_applied_replacement_migrations_skips_unreachable_database(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    closed = []

    class FakeConnection:
        def close(self) -> None:
            closed.append(True)

    class BrokenLoader:
        def __init__(self, connection: object, ignore_no_migrations: bool) -> None:
            raise env_refresh_module.OperationalError("database does not exist")

    monkeypatch.setattr(env_refresh_module, "connection", FakeConnection())
    monkeypatch.setattr(env_refresh_module, "MigrationLoader", BrokenLoader)

    assert env_refresh_module._real_record_fully_applied_replacement_migrations() == 0
    assert closed == [True]
    assert "Skipping replacement migration recording" in capsys.readouterr().out


def test_env_refresh_requires_explicit_write_for_missing_migrations(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_makemigrations(*args: str) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="model changes detected")

    monkeypatch.setattr(
        env_refresh_module,
        "_run_manage_makemigrations",
        fake_makemigrations,
    )

    with pytest.raises(
        CommandError, match="no longer writes migration files by default"
    ):
        env_refresh_module._check_or_write_migrations(
            ["nodes"],
            using_sqlite=True,
            default_db={"NAME": str(tmp_path / "db.sqlite3")},
            write_migrations=False,
        )


def test_env_refresh_can_write_migrations_when_explicit(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_makemigrations(*args: str) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(
            returncode=1 if len(calls) == 1 else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        env_refresh_module,
        "_run_manage_makemigrations",
        fake_makemigrations,
    )

    env_refresh_module._check_or_write_migrations(
        ["nodes"],
        using_sqlite=True,
        default_db={"NAME": str(tmp_path / "db.sqlite3")},
        write_migrations=True,
    )

    assert calls == [
        ("nodes", "--noinput"),
        ("nodes", "--merge", "--noinput"),
    ]


def test_env_refresh_does_not_delete_sqlite_for_generic_makemigrations_failure(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unlink_calls: list[Path] = []

    def fake_makemigrations(*args: str) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="syntax error")

    monkeypatch.setattr(
        env_refresh_module,
        "_run_manage_makemigrations",
        fake_makemigrations,
    )
    monkeypatch.setattr(env_refresh_module, "_unlink_sqlite_db", unlink_calls.append)

    with pytest.raises(CommandError, match="makemigrations failed"):
        env_refresh_module._check_or_write_migrations(
            ["nodes"],
            using_sqlite=True,
            default_db={"NAME": str(tmp_path / "db.sqlite3")},
            write_migrations=True,
        )

    assert unlink_calls == []


def test_env_refresh_retries_sqlite_for_inconsistent_migration_history(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    unlink_calls: list[Path] = []

    def fake_makemigrations(*args: str) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(
            returncode=0 if len(calls) == 3 else 1,
            stdout="",
            stderr="InconsistentMigrationHistory: broken",
        )

    monkeypatch.setattr(
        env_refresh_module,
        "_run_manage_makemigrations",
        fake_makemigrations,
    )
    monkeypatch.setattr(env_refresh_module, "_unlink_sqlite_db", unlink_calls.append)

    env_refresh_module._check_or_write_migrations(
        ["nodes"],
        using_sqlite=True,
        default_db={"NAME": str(tmp_path / "db.sqlite3")},
        write_migrations=True,
    )

    assert calls == [
        ("nodes", "--noinput"),
        ("nodes", "--merge", "--noinput"),
        ("nodes", "--noinput"),
    ]
    assert unlink_calls == [tmp_path / "db.sqlite3"]


def test_migration_check_app_labels_keeps_local_apps(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_app_root = tmp_path / "apps"
    local_app_root.mkdir()

    configs = [
        SimpleNamespace(
            label="core",
            name="apps.core",
            path=str(local_app_root / "core"),
        ),
        SimpleNamespace(
            label="nodes",
            name="apps.nodes",
            path=str(local_app_root / "nodes"),
        ),
        SimpleNamespace(
            label="django",
            name="django.contrib.admin",
            path="/usr/lib/django/admin",
        ),
    ]

    monkeypatch.setattr(
        env_refresh_module.settings,
        "INSTALLED_APPS",
        ["apps.core", "apps.nodes"],
        raising=False,
    )
    monkeypatch.setattr(
        env_refresh_module.apps,
        "get_app_configs",
        lambda: configs,
    )

    local_labels = env_refresh_module._real_local_app_labels()
    assert local_labels == ["core", "nodes"]
    assert env_refresh_module._migration_check_app_labels(local_labels) == local_labels


def test_local_app_labels_keep_current_runtime_apps(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_app_root = tmp_path / "apps"
    local_app_root.mkdir()

    configs = [
        SimpleNamespace(
            label="core",
            name="apps.core",
            path=str(local_app_root / "core"),
        ),
        SimpleNamespace(
            label="media",
            name="apps.media",
            path=str(local_app_root / "media"),
        ),
    ]

    monkeypatch.setattr(
        env_refresh_module.settings,
        "INSTALLED_APPS",
        ["apps.core", "apps.media"],
        raising=False,
    )
    monkeypatch.setattr(
        env_refresh_module.apps,
        "get_app_configs",
        lambda: configs,
    )

    local_labels = env_refresh_module._real_local_app_labels()
    assert local_labels == ["core", "media"]
    assert env_refresh_module._migration_check_app_labels(local_labels) == local_labels


def test_fixture_load_prescan_collects_user_mapping_outside_role_guard(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_path = tmp_path / "apps" / "users" / "fixtures" / "users__admin.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "model": "users.user",
                    "pk": 7,
                    "fields": {"username": "operator"},
                },
                {
                    "model": "nodes.noderole",
                    "pk": 3,
                    "fields": {"name": "Satellite"},
                },
            ]
        )
    )

    fake_existing = SimpleNamespace(pk=99)

    class FakeUserManager:
        def filter(self, **kwargs):
            assert kwargs == {"username": "operator"}
            return SimpleNamespace(first=lambda: fake_existing)

    fake_user_model = SimpleNamespace(objects=FakeUserManager())
    monkeypatch.setattr(env_refresh_module, "get_user_model", lambda: fake_user_model)

    def fake_get_model(model_label: str):
        if model_label == "users.user":
            return fake_user_model
        if model_label == "nodes.noderole":
            return SimpleNamespace()
        raise LookupError

    monkeypatch.setattr(env_refresh_module.apps, "get_model", fake_get_model)

    pending_role_names, user_pk_map = env_refresh_module._fixture_load_prescan(
        ["apps/users/fixtures/users__admin.json"]
    )

    assert pending_role_names == {"Satellite"}
    assert user_pk_map == {7: 99}


def test_sqlite_reset_forces_full_fixture_reload_after_scoped_selection(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixtures = [
        "apps/features/fixtures/features__changed.json",
        "apps/modules/fixtures/modules__unchanged.json",
    ]
    for fixture, model_label in [
        (fixtures[0], "features.item"),
        (fixtures[1], "modules.item"),
    ]:
        fixture_path = tmp_path / fixture
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(
            json.dumps([{"model": model_label, "pk": 1, "fields": {}}])
        )

    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir(exist_ok=True)
    (locks_dir / "migrations.md5").write_text("hash")
    (locks_dir / "fixtures.by-app.json").write_text(
        json.dumps({"features": "old", "modules": "same"})
    )

    class FeatureItem:
        _meta = SimpleNamespace(
            db_table="features_item",
            fields=[],
            label="features.Item",
        )

    class ModuleItem:
        _meta = SimpleNamespace(
            db_table="modules_item",
            fields=[],
            label="modules.Item",
        )

    class FakeSite:
        objects = SimpleNamespace()
        _meta = SimpleNamespace(db_table="django_site", fields=[], label="sites.Site")

    class FakeUserManager:
        def all(self):
            return []

        def filter(self, **kwargs):
            return SimpleNamespace(first=lambda: None)

    class FakeUser:
        objects = FakeUserManager()

    class FakeIntrospection:
        def __init__(self) -> None:
            self.calls = 0

        def table_names(self) -> list[str]:
            self.calls += 1
            if self.calls == 1:
                return ["features_item"]
            return ["features_item", "modules_item"]

    loaded_fixtures: list[str] = []
    migrate_calls = 0
    register_calls: list[str] = []

    def fake_get_model(*args):
        if args == ("sites", "Site"):
            return FakeSite
        models = {
            "features.item": FeatureItem,
            "modules.item": ModuleItem,
        }
        try:
            return models[args[0]]
        except KeyError as exc:
            raise LookupError from exc

    def fake_run_migrate(
        *,
        using_sqlite: bool,
        default_db: dict[str, str],
        interactive: bool,
    ) -> None:
        nonlocal migrate_calls
        migrate_calls += 1
        assert using_sqlite is True
        assert interactive is False

    def fake_load_fixtures(
        patched: dict[int, list[str]], *, using_sqlite: bool
    ) -> None:
        assert using_sqlite is True
        for priority in sorted(patched):
            for fixture in patched[priority]:
                loaded_fixtures.append(Path(fixture).relative_to(tmp_path).as_posix())

    monkeypatch.setattr(env_refresh_module, "_fixture_files", lambda: list(fixtures))
    monkeypatch.setattr(env_refresh_module, "_fixture_mtime_cache", lambda fixtures: {})
    monkeypatch.setattr(env_refresh_module, "_fixtures_hash", lambda fixtures: "new")
    monkeypatch.setattr(
        env_refresh_module,
        "_fixture_hashes_by_app",
        lambda fixtures: {"features": "new", "modules": "same"},
    )
    monkeypatch.setattr(env_refresh_module, "_pending_migration_graph", lambda: False)
    monkeypatch.setattr(env_refresh_module, "_schema_needs_migration", lambda: False)
    monkeypatch.setattr(env_refresh_module, "_run_migrate", fake_run_migrate)
    monkeypatch.setattr(env_refresh_module, "_ensure_content_types", lambda using: None)
    monkeypatch.setattr(env_refresh_module.apps, "get_model", fake_get_model)
    monkeypatch.setattr(env_refresh_module, "get_user_model", lambda: FakeUser)
    monkeypatch.setattr(
        env_refresh_module,
        "_load_fixtures_with_deferred_retry",
        fake_load_fixtures,
    )
    monkeypatch.setattr(
        env_refresh_module, "load_shared_user_fixtures", lambda force=True: None
    )
    monkeypatch.setattr(env_refresh_module, "load_user_fixtures", lambda user: None)
    monkeypatch.setattr(env_refresh_module, "generate_model_sigils", lambda: None)
    monkeypatch.setattr(env_refresh_module, "load_local_seed_zips", lambda: 0)
    monkeypatch.setattr(
        env_refresh_module.Node,
        "register_current",
        lambda notify_peers=False: (
            SimpleNamespace(public_endpoint="test.local"),
            False,
        ),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "_call_command_with_sqlite_lock_retry",
        lambda command, *, using_sqlite: register_calls.append(command),
    )
    monkeypatch.setattr(
        env_refresh_module,
        "connection",
        SimpleNamespace(
            alias="default",
            in_atomic_block=False,
            introspection=FakeIntrospection(),
        ),
    )

    env_refresh_module.run_database_tasks()

    assert migrate_calls == 1
    assert register_calls == ["register_site_apps", "register_site_apps"]
    assert set(loaded_fixtures) == set(fixtures)


def test_branch_tag_conflict_without_reconcile_fails_fast(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    migrate_calls = 0

    def fake_run_migrate(
        *, using_sqlite: bool, default_db: dict[str, str], interactive: bool
    ) -> None:
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


def test_content_types_are_ensured_when_migrate_is_skipped(
    env_refresh_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_migrate_calls = 0
    ensure_calls: list[str] = []
    fake_user_model = SimpleNamespace(objects=SimpleNamespace(all=lambda: []))

    def fake_run_migrate(
        *, using_sqlite: bool, default_db: dict[str, str], interactive: bool
    ) -> None:
        nonlocal run_migrate_calls
        run_migrate_calls += 1

    monkeypatch.setattr(env_refresh_module, "_run_migrate", fake_run_migrate)
    monkeypatch.setattr(env_refresh_module, "_schema_needs_migration", lambda: False)
    monkeypatch.setattr(env_refresh_module, "_pending_migration_graph", lambda: True)
    monkeypatch.setattr(
        env_refresh_module,
        "_ensure_content_types",
        lambda using="default": ensure_calls.append(using),
    )
    monkeypatch.setattr(env_refresh_module, "generate_model_sigils", lambda: None)
    monkeypatch.setattr(env_refresh_module, "get_user_model", lambda: fake_user_model)
    monkeypatch.setattr(
        env_refresh_module, "load_shared_user_fixtures", lambda force=True: None
    )
    monkeypatch.setattr(env_refresh_module, "load_local_seed_zips", lambda: 0)
    monkeypatch.setattr(
        env_refresh_module.Node,
        "register_current",
        lambda notify_peers=False: (
            SimpleNamespace(public_endpoint="test.local"),
            False,
        ),
    )

    env_refresh_module.run_database_tasks()

    assert run_migrate_calls == 0
    assert ensure_calls == ["default"]


def test_content_type_fixture_skip_reason_handles_role_profile_omissions(
    env_refresh_module: ModuleType,
) -> None:
    assert (
        env_refresh_module._content_type_reference_skip_reason(
            {"content_type": ["missing_profile_app", "thing"]}
        )
        == "missing app 'missing_profile_app'"
    )
    assert (
        env_refresh_module._content_type_reference_skip_reason(
            {"content_type": ["contenttypes", "missingmodel"]}
        )
        == "missing model 'contenttypes.missingmodel'"
    )
    assert (
        env_refresh_module._content_type_reference_skip_reason(
            {"content_type": ["contenttypes", "contenttype"]}
        )
        is None
    )
