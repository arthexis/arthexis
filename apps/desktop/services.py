"""Services for desktop extension registration and execution."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from apps.desktop.models import RegisteredExtension


@dataclass(slots=True)
class RegistrationResult:
    """Result of attempting an operating system extension registration."""

    success: bool
    message: str


def build_windows_registry_command(extension: RegisteredExtension) -> str:
    """Build the Windows shell command used to open a file with this extension."""

    python_executable = Path(sys.executable)
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    return (
        f'"{python_executable}" "{manage_py}" desktop_extension_open '
        f'--extension-id {extension.pk} --filename "%1"'
    )


def register_extension_with_os(extension: RegisteredExtension) -> RegistrationResult:
    """Register extension mapping in the local operating system when supported."""

    if not extension.pk:
        return RegistrationResult(False, "Extension must be saved before registration.")

    if sys.platform != "win32":
        command_preview = build_windows_registry_command(extension)
        return RegistrationResult(
            True,
            (
                "Non-Windows host detected. Registration skipped; "
                f"command preview: {command_preview}"
            ),
        )

    import winreg  # type: ignore[import-not-found]

    prog_id = f"Arthexis.Desktop{extension.extension.lstrip('.').upper()}"
    command_value = build_windows_registry_command(extension)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{extension.extension}") as ext_key:
        winreg.SetValueEx(ext_key, "", 0, winreg.REG_SZ, prog_id)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, fr"Software\Classes\{prog_id}") as progid_key:
        winreg.SetValueEx(progid_key, "", 0, winreg.REG_SZ, f"Arthexis handler for {extension.extension}")

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        fr"Software\Classes\{prog_id}\shell\open\command",
    ) as command_key:
        winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, command_value)

    return RegistrationResult(True, f"Registered {extension.extension} with command: {command_value}")


def run_registered_extension(extension: RegisteredExtension, filename: str | None) -> subprocess.CompletedProcess:
    """Execute the configured Django command for a registered extension."""

    command_parts, input_data = extension.build_runtime_command(filename)
    full_command = [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), *command_parts]
    return subprocess.run(
        full_command,
        input=(input_data or ""),
        text=True,
        capture_output=True,
        check=False,
    )
