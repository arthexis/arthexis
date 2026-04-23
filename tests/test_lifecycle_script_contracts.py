from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs/development/install-lifecycle-scripts-manual.md"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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
    )

    for section in expected_sections:
        assert section in manual, f"Missing manual section: {section}"

    assert "flowchart TD" in manual
    assert "install.sh" in manual and "status.sh" in manual and "command.sh" in manual


def test_install_usage_keeps_core_lifecycle_flags() -> None:
    install_script = _read("install.sh")
    expected_flags = (
        "--service NAME",
        "--port PORT",
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
        assert flag in install_script, f"install.sh usage is missing lifecycle flag: {flag}"


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
            "--branch)",
        ),
        "command.sh": (
            "Usage: ./command.sh <operational-command> [args...]",
            "Usage: ./command.sh list",
            "python -m utils.command_api",
        ),
    }

    for script_name, tokens in scripts_and_tokens.items():
        script_text = _read(script_name)
        for token in tokens:
            assert token in script_text, f"{script_name} is missing expected token: {token}"
