"""Regression tests for command.sh command listing behavior."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.regression


COMMAND_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "command.sh"
EXPECTED_USAGE = "Usage: $0 [--celery|--no-celery] [--deprecated] <command> [args...]"


@pytest.fixture(scope="module")
def command_script_contents() -> str:
    """Load command.sh source once for command-listing regression assertions."""
    return COMMAND_SCRIPT_PATH.read_text(encoding="utf-8")


def test_command_script_includes_dynamic_deprecated_filter_logic(command_script_contents: str) -> None:
    """The command helper should derive deprecated command filtering from metadata."""
    assert "--deprecated" in command_script_contents
    assert "show_deprecated=false" in command_script_contents
    assert "load_deprecated_absorbed_commands()" in command_script_contents
    assert "arthexis_absorbed_command" in command_script_contents
    assert "cached_command_output" in command_script_contents
    assert "if [ $# -eq 0 ]; then" in command_script_contents


def test_command_script_usage_documents_deprecated_flag(command_script_contents: str) -> None:
    """The script usage text should advertise the new --deprecated option."""
    assert EXPECTED_USAGE in command_script_contents
