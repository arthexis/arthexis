#!/usr/bin/env python3
"""Resolve the Arthexis virtual environment directory for local tooling."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import sys
from pathlib import Path


def file_digest(repo_root: Path, include_ci: bool, include_hardware: bool) -> str:
    files = [
        "requirements.txt",
        "pyproject.toml",
        "install.sh",
        "env-refresh.sh",
        "scripts/helpers/pip_install.py",
        "scripts/helpers/venv_path.py",
    ]
    if include_ci:
        files.append("requirements-ci.txt")
    if include_hardware:
        files.append("requirements-hw.txt")

    digest = hashlib.sha256()
    digest.update(
        f"python={sys.version_info.major}.{sys.version_info.minor}\0".encode()
    )
    digest.update(f"platform={sys.platform}\0".encode())
    digest.update(f"machine={platform.machine().lower()}\0".encode())
    for relative_name in files:
        path = repo_root / relative_name
        digest.update(relative_name.encode())
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def resolve_venv_dir(repo_root: Path, include_ci: bool, include_hardware: bool) -> Path:
    explicit_venv = os.environ.get("ARTHEXIS_VENV_DIR")
    if explicit_venv:
        return Path(explicit_venv).expanduser().resolve()

    env_root = os.environ.get("ARTHEXIS_ENV_ROOT")
    if env_root:
        python_tag = f"py{sys.version_info.major}.{sys.version_info.minor}"
        system_tag = sys.platform.replace(os.sep, "-")
        machine_tag = platform.machine().lower() or "unknown"
        cache_key = file_digest(repo_root, include_ci, include_hardware)
        return (
            Path(env_root).expanduser().resolve()
            / "venvs"
            / f"{python_tag}-{system_tag}-{machine_tag}-{cache_key}"
        )

    return repo_root / ".venv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("--include-ci", action="store_true")
    parser.add_argument("--include-hardware", action="store_true")
    args = parser.parse_args()

    print(
        resolve_venv_dir(
            args.repo_root.resolve(), args.include_ci, args.include_hardware
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
