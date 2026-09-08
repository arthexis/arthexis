from io import StringIO

import pytest
from django.core.management import call_command

from apps.core.system import identity


def test_node_role_uses_canonical_role(settings):
    settings.NODE_ROLE = "constellation"

    assert identity.node_role() == "Watchtower"


def test_version_reads_repository_version(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    (tmp_path / "VERSION").write_text("9.8.7\n", encoding="utf-8")

    assert identity.version() == "9.8.7"


def test_status_is_good_when_role_database_and_migrations_are_healthy(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, _query):
            return None

        def fetchone(self):
            return (1,)

    class Graph:
        @staticmethod
        def leaf_nodes():
            return []

    class Loader:
        graph = Graph()

    class Executor:
        loader = Loader()

        @staticmethod
        def migration_plan(_nodes):
            return []

    monkeypatch.setattr(identity, "node_role", lambda: "Control")
    monkeypatch.setattr(identity.connection, "cursor", lambda: Cursor())
    monkeypatch.setattr(identity, "MigrationExecutor", lambda _connection: Executor())

    assert identity.status() == "GOOD"


def test_status_fails_for_unknown_role(monkeypatch):
    monkeypatch.setattr(identity, "node_role", lambda: "Unknown")

    assert identity.status() == "FAIL"


def test_public_commands_expose_identity(monkeypatch):
    stdout = StringIO()
    monkeypatch.setattr("apps.core.management.commands.node_role.node_role", lambda: "Satellite")
    call_command("node_role", stdout=stdout)
    assert stdout.getvalue().strip() == "Satellite"

    stdout = StringIO()
    monkeypatch.setattr("apps.core.management.commands.version.version", lambda: "1.2.3")
    call_command("version", stdout=stdout)
    assert stdout.getvalue().strip() == "1.2.3"


def test_status_command_exits_nonzero_on_failure(monkeypatch):
    stdout = StringIO()
    monkeypatch.setattr("apps.core.management.commands.status.status", lambda: "FAIL")

    with pytest.raises(SystemExit) as exc:
        call_command("status", stdout=stdout)

    assert exc.value.code == 1
    assert stdout.getvalue().strip() == "FAIL"
