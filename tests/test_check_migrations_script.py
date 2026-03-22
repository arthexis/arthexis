"""Regression coverage for ``scripts/check_migrations.py``."""

from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path

from django.db.migrations.exceptions import IrreversibleError

from scripts import check_migrations


def test_staged_migration_files_include_added_and_modified(monkeypatch):
    result = subprocess.CompletedProcess(
        args=["git"],
        returncode=0,
        stdout=(
            "A apps/demo/migrations/0002_added.py\n"
            "M apps/demo/migrations/0003_updated.py\n"
            "R apps/demo/migrations/0004_renamed.py\n"
            "M apps/demo/models.py\n"
        ),
        stderr="",
    )
    monkeypatch.setattr(check_migrations.subprocess, "run", lambda *args, **kwargs: result)

    staged = check_migrations._staged_migration_files()

    assert staged == [
        check_migrations.StagedMigration(
            app_label="demo",
            migration_name="0002_added",
            path=Path("apps/demo/migrations/0002_added.py"),
        ),
        check_migrations.StagedMigration(
            app_label="demo",
            migration_name="0003_updated",
            path=Path("apps/demo/migrations/0003_updated.py"),
        ),
    ]


def test_migration_is_irreversible_uses_unapply_and_tuple_fallback():
    calls: list[tuple[object, bool]] = []

    class Loader:
        def project_state(self, nodes, at_end=True):
            calls.append((nodes, at_end))
            if isinstance(nodes, list):
                raise TypeError("list nodes unsupported")
            return "state-before-migration"

    class Connection:
        def schema_editor(self, *, collect_sql):
            assert collect_sql is True
            return nullcontext("schema-editor")

    class Migration:
        app_label = "demo"
        name = "0002_added"

        def __init__(self):
            self.unapply_calls: list[tuple[object, object, bool]] = []

        def unapply(self, project_state, schema_editor, collect_sql=False):
            self.unapply_calls.append((project_state, schema_editor, collect_sql))
            raise IrreversibleError("Operation RunPython in demo.0002_added is not reversible")

    migration = Migration()

    reason = check_migrations._migration_is_irreversible(Connection(), Loader(), migration)

    assert reason == "Operation RunPython in demo.0002_added is not reversible"
    assert calls == [
        ([("demo", "0002_added")], False),
        (("demo", "0002_added"), False),
    ]
    assert migration.unapply_calls == [("state-before-migration", "schema-editor", True)]
