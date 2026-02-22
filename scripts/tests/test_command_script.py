"""Regression tests for command.sh command listing behavior."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.regression


COMMAND_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "command.sh"


def test_command_script_includes_dynamic_deprecated_filter_logic() -> None:
    """The command helper should derive deprecated command filtering from metadata."""
    contents = COMMAND_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "--deprecated" in contents
    assert "show_deprecated=false" in contents
    assert "load_deprecated_absorbed_commands()" in contents
    assert "arthexis_absorbed_command" in contents
    assert "if [ $# -eq 0 ]; then" in contents


def test_command_script_usage_documents_deprecated_flag() -> None:
    """The script usage text should advertise the new --deprecated option."""
    contents = COMMAND_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "Usage: $0 [--celery|--no-celery] [--deprecated] <command> [args...]" in contents
