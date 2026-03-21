"""Regression tests for the liboqs cleanup migration source."""

from pathlib import Path

MIGRATION_PATH = Path("apps/core/migrations/0011_cleanup_removed_liboqs_app.py")


def test_liboqs_cleanup_migration_keeps_upgrade_and_rollback_paths() -> None:
    """The migration should clean stale liboqs state and recreate it on rollback."""

    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'schema_editor.execute("DROP TABLE IF EXISTS liboqs_oqsalgorithm")' in source
    assert 'ContentType.objects.filter(' in source
    assert 'app_label="liboqs"' in source
    assert 'model="oqsalgorithm"' in source
    assert 'Permission.objects.filter(content_type=content_type).delete()' in source
    assert 'CREATE TABLE IF NOT EXISTS liboqs_oqsalgorithm' in source
    assert 'add_oqsalgorithm' in source
    assert 'change_oqsalgorithm' in source
    assert 'delete_oqsalgorithm' in source
    assert 'view_oqsalgorithm' in source


def test_liboqs_cleanup_migration_declares_required_dependencies() -> None:
    """The migration should wait for auth, contenttypes, and the latest core migration."""

    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert '("auth", "0012_alter_user_first_name_max_length")' in source
    assert '("contenttypes", "0002_remove_content_type_name")' in source
    assert '("core", "0010_mcpapikey")' in source
