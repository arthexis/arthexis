"""Regression checks for migration deprecation marker operations."""

from __future__ import annotations

from types import SimpleNamespace

from utils.migration_branches import SafelyDeprecatedMigration


class _DummySchemaEditor:
    """Minimal schema editor stand-in for no-op migration operation tests."""

    connection = SimpleNamespace(alias="default")


def test_safely_deprecated_migration_is_a_noop_regression() -> None:
    """The marker operation should not mutate schema state in either direction."""

    op = SafelyDeprecatedMigration(reason="cleanup placeholder")
    schema_editor = _DummySchemaEditor()

    assert op.state_forwards("core", state=None) is None
    assert op.database_forwards("core", schema_editor, from_state=None, to_state=None) is None
    assert op.database_backwards("core", schema_editor, from_state=None, to_state=None) is None


def test_safely_deprecated_migration_deconstruct_roundtrip() -> None:
    """The operation must deconstruct with a stable import path and kwargs."""

    op = SafelyDeprecatedMigration(reason="legacy compatibility marker")

    path, args, kwargs = op.deconstruct()

    assert path == "utils.migration_branches.SafelyDeprecatedMigration"
    assert args == []
    assert kwargs == {"reason": "legacy compatibility marker"}
    assert op.describe() == "Deprecated no-op migration marker: legacy compatibility marker"
    assert op.migration_name_fragment == "safely_deprecated"
