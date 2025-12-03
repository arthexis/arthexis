"""Utilities for retroactive migration safety.

Provides branch sentry operations to detect databases that crossed
retroactively edited migrations without running the refreshed code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.migrations.operations.base import Operation


_MARKER_TABLE = "migration_branch_markers"


class MissingBranchSplinterError(RuntimeError):
    """Raised when a branch merge executes without a prior splinter marker."""

    def __init__(self, branch_id: str, *, merge_migration: str | None = None):
        hint = (
            "A retroactive migration edit was detected without a corresponding "
            "branch splinter marker. The database was migrated past the edited "
            "line using an outdated codebase."
        )
        message = hint
        if branch_id:
            message += f"\nBranch id: {branch_id}"
        if merge_migration:
            message += f"\nMerge migration: {merge_migration}"
        message += (
            "\nRecreate the database from a clean backup or roll it back to the "
            "first migration that contains the matching BranchSplinterOperation "
            "before applying this patch."
        )
        super().__init__(message)
        self.branch_id = branch_id
        self.merge_migration = merge_migration


class BranchTagConflictError(RuntimeError):
    """Raised when a rebuild guard detects an incompatible migration history."""

    def __init__(self, branch_id: str, migration_label: str, *, conflicts: list[str]):
        conflict_list = ", ".join(conflicts)
        message = (
            "This project rebuilt its migrations and detected existing entries "
            "from the previous branch. Apply the new migrations only on a clean "
            "database created after the rebuild."
        )
        message += f"\nBranch id: {branch_id}"
        message += f"\nGuard migration: {migration_label}"
        if conflict_list:
            message += f"\nExisting migrations: {conflict_list}"
        super().__init__(message)
        self.branch_id = branch_id
        self.migration_label = migration_label
        self.conflicts = conflicts


@dataclass(slots=True)
class _BranchMarker:
    branch_id: str
    splinter_migration: str | None
    merge_migration: str | None


class _BranchOperation(Operation):
    reduces_to_sql = True
    reversible = False

    def __init__(self, branch_id: str, *, migration_label: str | None = None):
        if not branch_id:
            raise ValueError("branch_id must be provided")
        self.branch_id = branch_id
        self.migration_label = migration_label

    def state_forwards(self, app_label: str, state: Any) -> None:  # pragma: no cover - no state change
        return None

    def database_backwards(self, app_label, schema_editor, from_state, to_state):  # pragma: no cover - irreversible
        raise NotImplementedError("Branch operations cannot be reversed")

    @staticmethod
    def _table_name(schema_editor) -> str:
        return schema_editor.connection.ops.quote_name(_MARKER_TABLE)

    @classmethod
    def _ensure_table(cls, schema_editor) -> None:
        table = cls._table_name(schema_editor)
        schema_editor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                branch_id VARCHAR(255) PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                splinter_migration VARCHAR(255),
                merge_migration VARCHAR(255)
            )
            """
        )

    @classmethod
    def _read_marker(cls, schema_editor, branch_id: str) -> _BranchMarker | None:
        table = cls._table_name(schema_editor)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT branch_id, splinter_migration, merge_migration FROM {table} WHERE branch_id = %s",
                [branch_id],
            )
            row = cursor.fetchone()
        if not row:
            return None
        return _BranchMarker(branch_id=row[0], splinter_migration=row[1], merge_migration=row[2])

    @classmethod
    def _upsert_marker(
        cls,
        schema_editor,
        *,
        branch_id: str,
        splinter_migration: str | None,
        merge_migration: str | None,
    ) -> None:
        table = cls._table_name(schema_editor)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT 1 FROM {table} WHERE branch_id = %s",
                [branch_id],
            )
            exists = cursor.fetchone() is not None
            if exists:
                cursor.execute(
                    f"""
                    UPDATE {table}
                       SET created_at = CURRENT_TIMESTAMP,
                           splinter_migration = %s,
                           merge_migration = %s
                     WHERE branch_id = %s
                    """,
                    [splinter_migration, merge_migration, branch_id],
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {table} (branch_id, created_at, splinter_migration, merge_migration)
                    VALUES (%s, CURRENT_TIMESTAMP, %s, %s)
                    """,
                    [branch_id, splinter_migration, merge_migration],
                )

    @classmethod
    def _delete_marker(cls, schema_editor, branch_id: str) -> None:
        table = cls._table_name(schema_editor)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table} WHERE branch_id = %s", [branch_id])


class BranchSplinterOperation(_BranchOperation):
    """Mark the start of a retroactively edited migration branch."""

    def describe(self) -> str:  # pragma: no cover - description only
        return f"Record branch splinter marker for {self.branch_id}"

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._ensure_table(schema_editor)
        self._upsert_marker(
            schema_editor,
            branch_id=self.branch_id,
            splinter_migration=self.migration_label,
            merge_migration=None,
        )

    def deconstruct(self):
        kwargs = {"branch_id": self.branch_id}
        if self.migration_label:
            kwargs["migration_label"] = self.migration_label
        return (
            f"{self.__class__.__module__}.{self.__class__.__qualname__}",
            [],
            kwargs,
        )


class BranchMergeOperation(_BranchOperation):
    """Validate that the splinter marker was encountered earlier in the graph."""

    def describe(self) -> str:  # pragma: no cover - description only
        return f"Validate branch merge marker for {self.branch_id}"

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._ensure_table(schema_editor)
        marker = self._read_marker(schema_editor, self.branch_id)
        if not marker:
            raise MissingBranchSplinterError(
                self.branch_id, merge_migration=self.migration_label
            )

        self._upsert_marker(
            schema_editor,
            branch_id=self.branch_id,
            splinter_migration=marker.splinter_migration,
            merge_migration=self.migration_label,
        )
        self._delete_marker(schema_editor, self.branch_id)

    def deconstruct(self):
        kwargs = {"branch_id": self.branch_id}
        if self.migration_label:
            kwargs["migration_label"] = self.migration_label
        return (
            f"{self.__class__.__module__}.{self.__class__.__qualname__}",
            [],
            kwargs,
        )


class BranchTagOperation(_BranchOperation):
    """Tag a rebuilt migration branch and block incompatible histories."""

    def __init__(
        self,
        branch_id: str,
        *,
        migration_label: str | None = None,
        project_apps: tuple[str, ...] | list[str] | set[str],
    ):
        super().__init__(branch_id, migration_label=migration_label)
        apps = tuple(sorted(set(project_apps)))
        if not apps:
            raise ValueError("project_apps must include at least one app label")
        self.project_apps = apps

    def describe(self) -> str:  # pragma: no cover - description only
        return f"Record rebuild branch tag for {self.branch_id}"

    def _existing_migrations(self, schema_editor) -> list[str]:
        placeholders = ", ".join(["%s"] * len(self.project_apps))
        query = (
            f"SELECT app, name FROM django_migrations WHERE app IN ({placeholders})"
        )
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(query, list(self.project_apps))
            rows = cursor.fetchall()
        return [f"{app}.{name}" for app, name in rows]

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._ensure_table(schema_editor)

        marker = self._read_marker(schema_editor, self.branch_id)
        if not marker:
            conflicts = self._existing_migrations(schema_editor)
            if conflicts:
                raise BranchTagConflictError(
                    self.branch_id,
                    self.migration_label or "(unknown)",
                    conflicts=conflicts,
                )

            self._upsert_marker(
                schema_editor,
                branch_id=self.branch_id,
                splinter_migration=self.migration_label,
                merge_migration=self.migration_label,
            )
            return

        self._upsert_marker(
            schema_editor,
            branch_id=self.branch_id,
            splinter_migration=marker.splinter_migration or self.migration_label,
            merge_migration=marker.merge_migration or self.migration_label,
        )

    def deconstruct(self):
        kwargs = {"branch_id": self.branch_id, "project_apps": self.project_apps}
        if self.migration_label:
            kwargs["migration_label"] = self.migration_label
        return (
            f"{self.__class__.__module__}.{self.__class__.__qualname__}",
            [],
            kwargs,
        )
