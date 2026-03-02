"""Regression tests for the shared command wrapper contract."""

from pathlib import Path
import subprocess

import pytest
from utils import command_api

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


def test_list_output_documents_canonical_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shared API should emit canonical list and run usage lines."""
    monkeypatch.setattr(
        command_api, "filtered_commands", lambda base_dir, options: ["alpha", "beta"]
    )
    exit_code = command_api.main(["list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Available Django management commands:" in captured.out
    assert "alpha" in captured.out
    assert "beta" in captured.out
    assert EXPECTED_LIST_USAGE in captured.out
    assert EXPECTED_RUN_USAGE in captured.out


def test_shared_api_preserves_absorbed_filter_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API should skip absorbed filtering only when --deprecated is enabled."""
    monkeypatch.setattr(
        command_api, "discover_commands", lambda base_dir, options: ["live", "legacy"]
    )
    monkeypatch.setattr(
        command_api, "discover_absorbed_commands", lambda base_dir: {"legacy"}
    )
    filtered_default = command_api.filtered_commands(
        Path.cwd(), command_api.CommandOptions()
    )
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


def test_read_cached_lines_handles_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cache read errors should degrade to a cache miss instead of crashing."""
    cache_file = tmp_path / "commands.txt"
    cache_file.write_text("alpha\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(Path, "stat", boom)
    assert command_api._read_cached_lines(cache_file, ttl_seconds=60) is None


def test_write_cache_lines_handles_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cache write errors should be ignored because caching is optional."""
    cache_file = tmp_path / ".cache" / "commands.txt"

    def boom(*args, **kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(Path, "mkdir", boom)
    command_api._write_cache_lines(cache_file, ["alpha", "beta"])
    assert not cache_file.exists(), "Cache file should not exist after a failed write"


def test_cache_ttl_seconds_falls_back_for_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache TTL should require positive integers and fallback otherwise."""
    monkeypatch.setenv("ARTHEXIS_COMMAND_CACHE_TTL", "45")
    assert command_api._cache_ttl_seconds() == 45

    monkeypatch.setenv("ARTHEXIS_COMMAND_CACHE_TTL", "0")
    assert command_api._cache_ttl_seconds() == command_api.DEFAULT_CACHE_TTL_SECONDS

    monkeypatch.setenv("ARTHEXIS_COMMAND_CACHE_TTL", "²")
    assert command_api._cache_ttl_seconds() == command_api.DEFAULT_CACHE_TTL_SECONDS


def test_manage_timeout_seconds_falls_back_for_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manage timeout should require positive integers and fallback otherwise."""
    monkeypatch.setenv("ARTHEXIS_MANAGE_TIMEOUT", "10")
    assert command_api._manage_timeout_seconds() == 10

    monkeypatch.setenv("ARTHEXIS_MANAGE_TIMEOUT", "-1")
    assert (
        command_api._manage_timeout_seconds()
        == command_api.DEFAULT_MANAGE_TIMEOUT_SECONDS
    )


def test_run_manage_raises_command_api_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Timeouts from manage.py should be reported as CommandApiError."""

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(command_api.subprocess, "run", timeout)
    with pytest.raises(command_api.CommandApiError, match="timed out"):
        command_api._run_manage(tmp_path, "help", "--commands")


def test_run_command_does_not_append_celery_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Execution should not force celery flags for commands that do not accept them."""

    captured: dict[str, list[str]] = {}

    class Result:
        returncode = 0

    def fake_run(cmd, cwd, check):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(
        command_api, "_resolve_command", lambda base_dir, raw, opts: "check"
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)
    exit_code = command_api.run_command(
        tmp_path, "check", ["--verbosity", "2"], command_api.CommandOptions(celery=True)
    )

    assert exit_code == 0
    assert captured["cmd"] == [
        command_api.sys.executable,
        "manage.py",
        "check",
        "--verbosity",
        "2",
    ]


def test_shell_and_batch_wrappers_document_matching_options(
    command_script_contents: str,
    command_batch_contents: str,
) -> None:
    """Static parity test for documented options in POSIX and Windows wrappers."""
    assert "-m utils.command_api" in command_script_contents
    assert "VENV_PYTHON=\".venv/bin/python\"" in command_script_contents
    assert "-m utils.command_api" in command_batch_contents
    for usage_fragment in (EXPECTED_LIST_USAGE, EXPECTED_RUN_USAGE):
        assert usage_fragment in command_script_contents
        assert usage_fragment in command_batch_contents
