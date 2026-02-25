"""Regression tests for the shared command wrapper contract."""

from pathlib import Path

import pytest

from utils import command_api


pytestmark = pytest.mark.regression


COMMAND_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "command.sh"
COMMAND_BATCH_PATH = Path(__file__).resolve().parents[2] / "command.bat"
EXPECTED_LIST_USAGE = "Usage: arthexis cmd list [--deprecated] [--celery|--no-celery]"
EXPECTED_RUN_USAGE = "Usage: arthexis cmd run [--deprecated] [--celery|--no-celery] <django-command> [args...]"


@pytest.fixture(scope="module")
def command_script_contents() -> str:
    """Load command.sh source once for static wrapper assertions."""
    return COMMAND_SCRIPT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def command_batch_contents() -> str:
    """Load command.bat source once for static wrapper assertions."""
    return COMMAND_BATCH_PATH.read_text(encoding="utf-8")


def test_list_output_documents_canonical_usage(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The shared API should emit canonical list and run usage lines."""

    monkeypatch.setattr(command_api, "filtered_commands", lambda base_dir, options: ["alpha", "beta"])

    exit_code = command_api.main(["list"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Available Django management commands:" in captured.out
    assert "alpha" in captured.out
    assert "beta" in captured.out
    assert EXPECTED_LIST_USAGE in captured.out
    assert EXPECTED_RUN_USAGE in captured.out


def test_shared_api_preserves_absorbed_filter_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API should skip absorbed filtering only when --deprecated is enabled."""

    monkeypatch.setattr(command_api, "discover_commands", lambda base_dir, options: ["live", "legacy"])
    monkeypatch.setattr(command_api, "discover_absorbed_commands", lambda base_dir: {"legacy"})

    filtered_default = command_api.filtered_commands(Path.cwd(), command_api.CommandOptions())
    filtered_with_deprecated = command_api.filtered_commands(
        Path.cwd(), command_api.CommandOptions(deprecated=True)
    )

    assert filtered_default == ["live"]
    assert filtered_with_deprecated == ["live", "legacy"]


def test_shared_api_normalizes_hyphen_and_validates_command_name() -> None:
    """Hyphen to underscore normalization and validation should match legacy behavior."""

    assert command_api.normalize_command_name("check-time") == "check_time"
    with pytest.raises(ValueError, match="Invalid command name"):
        command_api.normalize_command_name("check*time")


def test_shell_and_batch_wrappers_document_matching_options(
    command_script_contents: str,
    command_batch_contents: str,
) -> None:
    """Static parity test for documented options in POSIX and Windows wrappers."""

    assert "python -m utils.command_api" in command_script_contents
    assert "-m utils.command_api" in command_batch_contents

    for usage_fragment in (EXPECTED_LIST_USAGE, EXPECTED_RUN_USAGE):
        assert usage_fragment in command_script_contents
        assert usage_fragment in command_batch_contents
