"""Tests for the consolidated ``test`` management command."""

from __future__ import annotations

import json
import sys

import pytest
from django.core.management import CommandError, call_command

from apps.tests.discovery import (
    MAX_NODE_ID_LENGTH,
    _infer_app_label,
    _normalize_marks,
    discover_suite_tests,
)
from apps.tests.models import SuiteTest

pytestmark = pytest.mark.pr_origin(6302)


def test_test_command_rejects_unknown_action() -> None:
    """Regression: unsupported actions should raise a command error."""

    from apps.tests.management.commands.test import Command

    with pytest.raises(CommandError, match="Unsupported action"):
        Command().handle(action="invalid", pytest_args=[])


def test_test_server_subcommand_does_not_require_vscode_cli(monkeypatch) -> None:
    """Regression: ``test server`` should execute via Python module imports only."""

    from apps.tests.management.commands.test import Command

    called: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 0

    monkeypatch.setattr("utils.devtools.test_server.main", fake_main)

    Command()._run_test_server(interval=1.5, debounce=0.5, latest=True)

    assert called["argv"] == ["--interval", "1.5", "--debounce", "0.5", "--latest"]


@pytest.mark.django_db
def test_discover_subcommand_refreshes_suite_tests(monkeypatch) -> None:
    """Regression: ``test discover`` should replace persisted suite metadata."""

    class FakeQuerySet:
        def delete(self) -> tuple[int, dict[str, int]]:
            return 1, {"tests.SuiteTest": 1}

    captured: dict[str, object] = {}

    class FakeManager:
        def all(self) -> FakeQuerySet:
            return FakeQuerySet()

        def bulk_create(self, items):
            captured["items"] = items
            return items

    monkeypatch.setattr(SuiteTest, "objects", FakeManager())

    monkeypatch.setattr(
        "apps.tests.management.commands.test.discover_suite_tests",
        lambda: [
            {
                "node_id": "apps/tests/test_test_management_command.py::test_discover_subcommand_refreshes_suite_tests",
                "name": "test_discover_subcommand_refreshes_suite_tests",
                "module_path": "apps.tests.test_test_management_command",
                "app_label": "tests",
                "class_name": "",
                "marks": ["regression"],
                "file_path": "apps/tests/test_test_management_command.py",
                "is_parameterized": False,
            }
        ],
    )

    call_command("test", "discover")

    created = captured["items"]
    assert len(created) == 1
    assert created[0].node_id == (
        "apps/tests/test_test_management_command.py::"
        "test_discover_subcommand_refreshes_suite_tests"
    )
    assert created[0].app_label == "tests"
    assert created[0].marks == ["regression"]


def test_normalize_marks_filters_builtin_keywords() -> None:
    """Regression: marker normalization should keep only useful custom marks."""

    marks = _normalize_marks(["regression", "django_db", "", "slow", "parametrize"])

    assert marks == ["regression", "slow"]


def test_infer_app_label_from_apps_path() -> None:
    """Infer app labels from repository paths under ``apps/``."""

    assert _infer_app_label("apps/tests/test_test_management_command.py") == "tests"
    assert _infer_app_label("tests/test_misc.py") == ""


def test_discover_suite_tests_truncates_long_node_ids(monkeypatch) -> None:
    """Regression: discovery should cap node ids to the SuiteTest field length."""

    long_node_id = "apps/tests/test_long.py::test_case[" + ("x" * 700) + "]"
    payload = {
        "returncode": 0,
        "items": [
            {
                "node_id": long_node_id,
                "name": "test_case",
                "file_path": "apps/tests/test_long.py",
                "module_path": "apps.tests.test_long",
                "class_name": "",
                "marks": ["regression"],
            }
        ],
    }

    class FakeCompletedProcess:
        stdout = "collected 1 item\n" + json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: FakeCompletedProcess()
    )

    tests = discover_suite_tests()

    assert len(tests[0]["node_id"]) == MAX_NODE_ID_LENGTH
    assert tests[0]["node_id"] == long_node_id[:MAX_NODE_ID_LENGTH]


@pytest.mark.django_db
def test_discover_subcommand_is_atomic_on_bulk_create_failure(monkeypatch) -> None:
    """Regression: failed inserts should rollback deletions during refresh."""

    SuiteTest.objects.create(
        node_id="apps/tests/test_existing.py::test_existing",
        name="test_existing",
    )

    monkeypatch.setattr(
        "apps.tests.management.commands.test.discover_suite_tests",
        lambda: [
            {
                "node_id": "apps/tests/test_new.py::test_new",
                "name": "test_new",
                "module_path": "apps.tests.test_new",
                "app_label": "tests",
                "class_name": "",
                "marks": [],
                "file_path": "apps/tests/test_new.py",
                "is_parameterized": False,
            }
        ],
    )

    original_bulk_create = SuiteTest.objects.bulk_create

    def raising_bulk_create(*args, **kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(SuiteTest.objects, "bulk_create", raising_bulk_create)

    with pytest.raises(RuntimeError, match="insert failed"):
        call_command("test", "discover")

    monkeypatch.setattr(SuiteTest.objects, "bulk_create", original_bulk_create)

    assert SuiteTest.objects.filter(
        node_id="apps/tests/test_existing.py::test_existing"
    ).exists()
