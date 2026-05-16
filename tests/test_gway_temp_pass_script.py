from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gway-temp-pass.bat"


def test_gway_temp_pass_script_supports_create_option() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'if /I "%~1"=="--create"' in script
    assert 'set "CREATE_USER=1"' in script
    assert 'set "CREATE_ARG=--create"' in script
    assert "--create              Create the user remotely if it does not exist." in script
    assert "gway-temp-pass.bat --service porsche --create" in script


def test_gway_temp_pass_script_forwards_create_to_password_command() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert ".venv/bin/python manage.py password" in script
    assert "--temporary --expires-in %EXPIRY_SECONDS% --allow-change %CREATE_ARG%" in script
    assert "cd /home/ubuntu/%SERVICE_DIR%" in script
    assert "%GW_REMOTE_SSH%" in script
    assert "%GW_PROD_TARGET%" in script


def test_gway_temp_pass_script_validates_nested_ssh_inputs() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "call :validate_identifier" in script
    assert "call :validate_service" in script
    assert "call :validate_expiry" in script
    assert "Invalid --user value" in script
    assert "Invalid --service value" in script
