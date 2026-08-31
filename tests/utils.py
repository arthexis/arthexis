from __future__ import annotations

import os
import subprocess
from pathlib import Path


_BASH_PATH_STYLE: str | None = None


def _detect_bash_path_style() -> str:
    global _BASH_PATH_STYLE
    if _BASH_PATH_STYLE is not None:
        return _BASH_PATH_STYLE

    override = os.environ.get("ARTHEXIS_BASH_PATH_STYLE")
    if override:
        _BASH_PATH_STYLE = override
        return _BASH_PATH_STYLE

    if os.name != "nt":
        _BASH_PATH_STYLE = "posix"
        return _BASH_PATH_STYLE

    style = "msys"
    try:
        pwd_result = subprocess.run(
            ["bash", "-lc", "pwd"],
            check=False,
            capture_output=True,
            text=True,
        )
        if pwd_result.returncode == 0:
            pwd = pwd_result.stdout.strip()
            if pwd.startswith("/mnt/"):
                style = "wsl"
    except FileNotFoundError:
        pass

    _BASH_PATH_STYLE = style

    return _BASH_PATH_STYLE


def bash_path(path: Path) -> str:
    posix_path = path.as_posix()
    if os.name != "nt":
        return posix_path
    if len(posix_path) > 1 and posix_path[1] == ":":
        drive = posix_path[0].lower()
        style = _detect_bash_path_style()
        if style == "wsl":
            return f"/mnt/{drive}{posix_path[2:]}"
        return f"/{drive}{posix_path[2:]}"
    return posix_path
