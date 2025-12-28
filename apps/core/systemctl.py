from __future__ import annotations

import shutil
import subprocess


def _systemctl_command() -> list[str]:
    """Return the base systemctl command, preferring sudo when available."""

    if shutil.which("systemctl") is None:
        return []

    sudo_path = shutil.which("sudo")
    if sudo_path is None:
        return ["systemctl"]

    try:
        sudo_ready = subprocess.run(
            [sudo_path, "-n", "true"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        sudo_ready = None

    if sudo_ready is not None and sudo_ready.returncode == 0:
        return [sudo_path, "-n", "systemctl"]

    return ["systemctl"]
