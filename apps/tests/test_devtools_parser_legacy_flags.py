"""Regression tests for removed legacy devtools parser flags."""

from __future__ import annotations

import pytest

from utils.devtools import migration_server, test_server


@pytest.mark.parametrize(
    ("parser_func", "legacy_flag"),
    [
        (test_server.parse_args, "--interval"),
        (test_server.parse_args, "--debounce"),
        (test_server.parse_args, "--latest"),
        (test_server.parse_args, "--no-latest"),
        (migration_server.parse_args, "--server"),
        (migration_server.parse_args, "--latest"),
        (migration_server.parse_args, "--no-latest"),
    ],
)
def test_parsers_reject_removed_legacy_flags(
    parser_func, legacy_flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removed legacy parser flags should fail fast with argparse errors."""

    with pytest.raises(SystemExit, match="2"):
        parser_func([legacy_flag])

    captured = capsys.readouterr()
    assert "unrecognized arguments" in captured.err
