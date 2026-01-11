from __future__ import annotations

import os
from pathlib import Path


def bash_path(path: Path) -> str:
    posix_path = path.as_posix()
    if os.name != "nt":
        return posix_path
    if len(posix_path) > 1 and posix_path[1] == ":":
        drive = posix_path[0].lower()
        return f"/{drive}{posix_path[2:]}"
    return posix_path
