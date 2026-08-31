from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import get_commands

from utils import command_api
from utils.command_api import (
    COMMAND_ALIASES,
    SUPPORTED_OPERATIONAL_COMMANDS,
    CommandApiError,
    list_commands,
)

ROOT = Path(__file__).resolve().parents[1]


def test_command_api_allowlist_is_sorted_and_unique() -> None:
    commands = list(SUPPORTED_OPERATIONAL_COMMANDS)

    assert commands == sorted(commands)
    assert len(commands) == len(set(commands))


def test_command_api_allowlist_resolves_to_management_commands() -> None:
    available_commands = set(get_commands())
    resolved_commands = {
        COMMAND_ALIASES.get(command, command)
        for command in SUPPORTED_OPERATIONAL_COMMANDS
    }

    assert set(COMMAND_ALIASES) <= set(SUPPORTED_OPERATIONAL_COMMANDS)
    assert resolved_commands <= available_commands


def test_command_api_keeps_pr_oversee_out_of_lifecycle_wrapper() -> None:
    assert "pr_oversee" not in SUPPORTED_OPERATIONAL_COMMANDS

    with pytest.raises(CommandApiError, match="Unsupported operational command"):
        command_api.run_command(ROOT, "pr_oversee", [])


def test_command_api_resolves_diagnose_to_diagnostics(monkeypatch) -> None:
    calls = []

    def fake_run(args, *, cwd, check):
        calls.append((args, cwd, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        command_api, "resolve_project_python", lambda _base_dir: "python"
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)

    assert command_api.run_command(ROOT, "diagnose", ["analyze"]) == 0
    assert calls == [(["python", "manage.py", "diagnostics", "analyze"], ROOT, False)]


@pytest.mark.parametrize(
    ("command_name", "command_args", "expected_args"),
    [
        ("nginx", ["configure"], ["--configure"]),
        (
            "nginx",
            ["configure", "--mode", "public"],
            ["--configure", "--mode", "public"],
        ),
        ("https", ["enable"], ["--enable", "--local"]),
        ("https", ["enable", "--no-sudo"], ["--enable", "--local", "--no-sudo"]),
        ("release", ["apply-migrations"], ["apply-migrations"]),
    ],
)
def test_command_api_supports_namespaced_operational_commands(
    monkeypatch, command_name: str, command_args: list[str], expected_args: list[str]
) -> None:
    calls = []

    def fake_run(args, *, cwd, check):
        calls.append((args, cwd, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        command_api, "resolve_project_python", lambda _base_dir: "python"
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)

    assert command_api.run_command(ROOT, command_name, command_args) == 0
    assert calls == [
        (["python", "manage.py", command_name, *expected_args], ROOT, False)
    ]


def test_command_api_resolves_cwd_sigil_from_environment(monkeypatch) -> None:
    calls = []

    def fake_run(args, *, cwd, check):
        calls.append((args, cwd, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        command_api, "resolve_project_python", lambda _base_dir: "python"
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)
    monkeypatch.setenv("ARTHEXIS_CALLER_CWD", "/tmp/operator-run-dir")

    assert command_api.run_command(ROOT, "repo", ["sync", "[CWD]"]) == 0
    assert calls == [
        (["python", "manage.py", "repo", "sync", "/tmp/operator-run-dir"], ROOT, False)
    ]


def test_command_api_ignores_relative_cwd_sigil_environment(monkeypatch) -> None:
    calls = []

    def fake_run(args, *, cwd, check):
        calls.append((args, cwd, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        command_api, "resolve_project_python", lambda _base_dir: "python"
    )
    monkeypatch.setattr(command_api.subprocess, "run", fake_run)
    monkeypatch.setenv("ARTHEXIS_CALLER_CWD", "relative/operator-run-dir")

    assert command_api.run_command(ROOT, "repo", ["sync", "[CWD]"]) == 0
    assert calls == [(["python", "manage.py", "repo", "sync", "[CWD]"], ROOT, False)]


def test_list_commands_prints_compact_command_list(capsys) -> None:
    list_commands()

    output = capsys.readouterr().out
    lines = output.splitlines()
    command_lines = lines[1 : lines.index("")]

    assert any(", " in line for line in command_lines)
    assert len(command_lines) < len(SUPPORTED_OPERATIONAL_COMMANDS) // 2
    assert "ocpp" in "\n".join(command_lines)


def test_operational_commands_doc_matches_command_api_allowlist() -> None:
    docs = (ROOT / "docs" / "operations" / "operational-commands.md").read_text(
        encoding="utf-8"
    )
    section_match = re.search(
        r"## Supported operational commands\n\n(?P<body>.*?)\n## Notes",
        docs,
        flags=re.DOTALL,
    )
    assert section_match is not None

    documented_commands = re.findall(
        r"^- `([^`]+)`$", section_match.group("body"), re.MULTILINE
    )

    assert documented_commands == list(SUPPORTED_OPERATIONAL_COMMANDS)
