from __future__ import annotations

from tests.plugins import sqlite_paths


def test_configure_ephemeral_sqlite_paths_records_generated_paths(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("ARTHEXIS_SQLITE_PATH", raising=False)
    monkeypatch.delenv("ARTHEXIS_SQLITE_TEST_PATH", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(sqlite_paths, "_PYTEST_SQLITE_TMP_DIR", tmp_path)
    monkeypatch.setattr(sqlite_paths, "_SQLITE_PATH_SOURCES", {})

    sqlite_paths.configure_ephemeral_sqlite_paths()

    assert sqlite_paths.sqlite_env_summary() == {
        "ARTHEXIS_SQLITE_PATH": {
            "path": str(tmp_path / "default-main.sqlite3"),
            "source": "generated",
        },
        "ARTHEXIS_SQLITE_TEST_PATH": {
            "path": str(tmp_path / "test-main.sqlite3"),
            "source": "generated",
        },
    }
    assert sqlite_paths.sqlite_env_summary_lines() == [
        f"ARTHEXIS_SQLITE_PATH={tmp_path / 'default-main.sqlite3'} (generated)",
        f"ARTHEXIS_SQLITE_TEST_PATH={tmp_path / 'test-main.sqlite3'} (generated)",
    ]


def test_configure_ephemeral_sqlite_paths_preserves_caller_paths(
    monkeypatch,
    tmp_path,
):
    default_path = tmp_path / "caller-default.sqlite3"
    test_path = tmp_path / "caller-test.sqlite3"
    monkeypatch.setenv("ARTHEXIS_SQLITE_PATH", str(default_path))
    monkeypatch.setenv("ARTHEXIS_SQLITE_TEST_PATH", str(test_path))
    monkeypatch.setattr(sqlite_paths, "_PYTEST_SQLITE_TMP_DIR", tmp_path / "generated")
    monkeypatch.setattr(sqlite_paths, "_SQLITE_PATH_SOURCES", {})

    sqlite_paths.configure_ephemeral_sqlite_paths()

    assert sqlite_paths.sqlite_env_summary() == {
        "ARTHEXIS_SQLITE_PATH": {
            "path": str(default_path),
            "source": "caller-provided",
        },
        "ARTHEXIS_SQLITE_TEST_PATH": {
            "path": str(test_path),
            "source": "caller-provided",
        },
    }
