import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.slow]

if os.getenv("RUN_SLOW_TESTS") != "1":
    pytest.skip("RUN_SLOW_TESTS=1 required for env refresh integration tests.", allow_module_level=True)


@pytest.fixture(scope="session")
def env_refresh_module():
    path = Path(settings.BASE_DIR) / "env-refresh.py"
    spec = importlib.util.spec_from_file_location("env_refresh", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def refreshed_environment(env_refresh_module, django_db_setup, django_db_blocker):
    base_dir = Path(settings.BASE_DIR)
    locks_dir = base_dir / ".locks"
    if locks_dir.exists():
        shutil.rmtree(locks_dir)

    existing_md5_files = {path.name for path in base_dir.glob("*.md5")}
    existing_lock_files = {path.name for path in base_dir.glob("*.lock")}

    with django_db_blocker.unblock():
        env_refresh_module.main(["database"], latest=True)
        executor = MigrationExecutor(connection)
        migration_plan = executor.migration_plan(executor.loader.graph.leaf_nodes())

        migrations_hash = (locks_dir / "migrations.md5").read_text().strip()
        expected_migrations_hash = env_refresh_module._migration_hash(
            env_refresh_module._local_app_labels()
        )

        fixtures_hash = (locks_dir / "fixtures.md5").read_text().strip()
        fixtures = env_refresh_module._fixture_files()
        expected_fixtures_hash = env_refresh_module._fixtures_hash(fixtures) if fixtures else ""
        fixtures_cache = json.loads((locks_dir / "fixtures.by-app.json").read_text())
        expected_fixture_cache = (
            env_refresh_module._fixture_hashes_by_app(fixtures) if fixtures else {}
        )

    md5_files = {path.name for path in base_dir.glob("*.md5")}
    lock_files = {path.name for path in base_dir.glob("*.lock")}

    return {
        "locks_dir": locks_dir,
        "migration_plan": migration_plan,
        "migrations_hash": migrations_hash,
        "expected_migrations_hash": expected_migrations_hash,
        "fixtures_hash": fixtures_hash,
        "expected_fixtures_hash": expected_fixtures_hash,
        "fixtures_cache": fixtures_cache,
        "expected_fixture_cache": expected_fixture_cache,
        "new_md5_files": md5_files - existing_md5_files,
        "new_lock_files": lock_files - existing_lock_files,
    }


def test_env_refresh_applies_all_migrations(refreshed_environment):
    assert refreshed_environment["migration_plan"] == []


def test_env_refresh_records_migration_state(refreshed_environment):
    assert refreshed_environment["migrations_hash"] == refreshed_environment[
        "expected_migrations_hash"
    ]


def test_env_refresh_loads_seed_fixtures(refreshed_environment, env_refresh_module):
    locks_dir = refreshed_environment["locks_dir"]
    fixture_hash_file = locks_dir / "fixtures.md5"

    assert fixture_hash_file.exists()
    assert fixture_hash_file.read_text().strip() == refreshed_environment[
        "fixtures_hash"
    ]
    assert refreshed_environment["fixtures_hash"] == refreshed_environment[
        "expected_fixtures_hash"
    ]

    if env_refresh_module._fixture_files():
        assert refreshed_environment["fixtures_hash"]


def test_env_refresh_records_per_app_fixture_hashes(refreshed_environment):
    assert refreshed_environment["fixtures_cache"] == refreshed_environment[
        "expected_fixture_cache"
    ]


def test_env_refresh_leaves_no_pending_migrations(refreshed_environment, env_refresh_module):
    migrations_hash_before = refreshed_environment["migrations_hash"]

    call_command(
        "makemigrations",
        *env_refresh_module._local_app_labels(),
        dry_run=True,
        check=True,
    )

    migrations_hash_after = (
        refreshed_environment["locks_dir"] / "migrations.md5"
    ).read_text().strip()
    assert migrations_hash_after == migrations_hash_before

    executor = MigrationExecutor(connection)
    assert executor.loader.detect_conflicts() == {}


def test_env_refresh_does_not_create_md5_or_lockfiles_in_base_dir(refreshed_environment):
    assert not refreshed_environment["new_md5_files"]
    assert not refreshed_environment["new_lock_files"]

