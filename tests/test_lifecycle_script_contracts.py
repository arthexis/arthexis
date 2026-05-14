from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs/development/install-lifecycle-scripts-manual.md"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _read_shell_contract(path: str) -> str:
    return "\n".join(
        line for line in _read(path).splitlines() if not line.lstrip().startswith("#")
    )


def _read_usage_block(path: str) -> str:
    script_text = _read(path)
    usage_match = re.search(
        r"^usage\(\)\s*\{\n(?P<body>.*?)^\}\n",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    assert usage_match, f"{path} is missing usage() block"
    return usage_match.group("body")


def test_lifecycle_manual_covers_operator_entrypoints() -> None:
    manual = MANUAL.read_text(encoding="utf-8")

    expected_sections = (
        "## 1. Installation (`install.sh`)",
        "## 2.1 Startup (`start.sh`)",
        "## 2.2 Shutdown (`stop.sh`)",
        "## 3. Upgrades (`upgrade.sh`)",
        "## 4. Runtime reconfiguration (`configure.sh`)",
        "## 5. Runtime status (`status.sh`)",
        "## 6. Operational command entrypoint (`command.sh`)",
        "## 7. Uninstall (`uninstall.sh`)",
        "## 8. Error report (`error-report.sh`)",
    )

    for section in expected_sections:
        assert section in manual, f"Missing manual section: {section}"

    assert "flowchart TD" in manual
    assert "install.sh" in manual and "status.sh" in manual and "command.sh" in manual


def test_install_usage_keeps_core_lifecycle_flags() -> None:
    install_usage = _read_usage_block("install.sh")
    expected_flags = (
        "--service",
        "--port",
        "--upgrade",
        "--clean",
        "--repair",
        "--start",
        "--no-start",
        "--satellite",
        "--terminal",
        "--control",
        "--watchtower",
    )

    for flag in expected_flags:
        assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", install_usage), (
            f"install.sh usage is missing lifecycle flag: {flag}"
        )


def test_install_debian_default_requires_redis_for_terminal_celery() -> None:
    install_script = _read_shell_contract("install.sh")
    default_match = re.search(
        r"if \[ \$\{#ORIGINAL_ARGS\[@\]\} -eq 0 \] && is_debian_host; then\n"
        r"(?P<body>.*?)\nfi",
        install_script,
        re.DOTALL,
    )
    assert default_match, "install.sh is missing Debian no-args default branch"

    body = default_match.group("body")
    assert 'SERVICE="arthexis"' in body
    assert "ENABLE_CELERY=true" in body
    assert 'NODE_ROLE="Terminal"' not in body
    assert "REQUIRES_REDIS=true" in body


def test_lifecycle_scripts_expose_documented_entrypoints() -> None:
    scripts_and_tokens = {
        "start.sh": ("--clear-logs", "--show LEVEL", "--reload", "--silent"),
        "stop.sh": ("--all", "--force", "--confirm"),
        "status.sh": ("Usage: ./status.sh", "--wait", "--help"),
        "configure.sh": (
            "--feature SLUG",
            "--feature-param FEATURE:KEY=VALUE",
            "--repair",
            "--check",
        ),
        "upgrade.sh": (
            "--detached",
            "--reconcile",
            "--migrate",
            "--stop",
            "--branch",
        ),
        "error-report.sh": (
            "Usage: ./error-report.sh",
            "--output-dir DIR",
            "--upload-url URL",
            "--dry-run",
            "sys.version_info[0] == 3",
        ),
        "uninstall.sh": ("--service NAME", "--no-warn", "--rfid-service", "--no-rfid-service"),
    }

    for script_name, tokens in scripts_and_tokens.items():
        script_text = _read_shell_contract(script_name)
        for token in tokens:
            assert token in script_text, f"{script_name} is missing expected token: {token}"

    upgrade_script = _read_shell_contract("upgrade.sh")
    assert re.search(r"^\s*--branch\)\s*$", upgrade_script, re.MULTILINE), (
        "upgrade.sh is missing expected parser label: --branch)"
    )

    command_script = _read_shell_contract("command.sh")
    assert 'python -m utils.command_api "$@"' in command_script

    manual = MANUAL.read_text(encoding="utf-8")
    assert "`./command.sh list`" in manual
    assert "`./command.sh <operational-command> [args...]`" in manual
    assert "`error-report.sh` builds a single diagnostic zip" in manual
