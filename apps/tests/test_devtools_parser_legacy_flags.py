"""Regression tests for removed legacy devtools parser flags."""

from __future__ import annotations

import pytest

from utils.devtools import migration_server, test_server


@pytest.mark.parametrize(
    "legacy_flag",
    ["--interval", "--debounce", "--latest", "--no-latest"],
)
def test_test_server_parser_rejects_removed_legacy_flags(
    legacy_flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Legacy compatibility options for the test server should fail fast."""

    with pytest.raises(SystemExit, match="2"):
        test_server.parse_args([legacy_flag])

    captured = capsys.readouterr()
    assert "unrecognized arguments" in captured.err


@pytest.mark.parametrize("legacy_flag", ["--server", "--latest", "--no-latest"])
def test_migration_server_parser_rejects_removed_alias_flags(
    legacy_flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removed migration server alias flags should fail fast."""

    with pytest.raises(SystemExit, match="2"):
        migration_server.parse_args([legacy_flag])

    captured = capsys.readouterr()
    assert "unrecognized arguments" in captured.err

