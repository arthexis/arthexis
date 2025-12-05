import importlib.util
import shutil
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.nodes.models import Node
from apps.sigils.models import SigilRoot


@pytest.fixture(scope="session")
def env_refresh_module():
    path = Path(settings.BASE_DIR) / "env-refresh.py"
    spec = importlib.util.spec_from_file_location("env_refresh", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.django_db(transaction=True)
def test_env_refresh_applies_and_records_state(env_refresh_module):
    locks_dir = Path(settings.BASE_DIR) / ".locks"
    if locks_dir.exists():
        shutil.rmtree(locks_dir)

    env_refresh_module.main(["database"], latest=True)

    executor = MigrationExecutor(connection)
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []

    migrations_hash = (locks_dir / "migrations.md5").read_text().strip()
    expected_migrations_hash = env_refresh_module._migration_hash(
        env_refresh_module._local_app_labels()
    )
    assert migrations_hash == expected_migrations_hash

    assert Node.objects.exists()
    assert SigilRoot.objects.filter(prefix="NODE").exists()

    fixtures_hash = (locks_dir / "fixtures.md5").read_text().strip()
    fixtures = env_refresh_module._fixture_files()
    expected_fixtures_hash = env_refresh_module._fixtures_hash(fixtures) if fixtures else ""
    assert fixtures_hash == expected_fixtures_hash

    migrations_hash_before = migrations_hash
    call_command("makemigrations", *env_refresh_module._local_app_labels(), dry_run=True, check=True)
    migrations_hash_after = (locks_dir / "migrations.md5").read_text().strip()
    assert migrations_hash_after == migrations_hash_before
    assert executor.loader.detect_conflicts() == {}
