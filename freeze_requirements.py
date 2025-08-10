#!/usr/bin/env python
"""Freeze dependencies without dropping environment markers."""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def freeze(req_file: Path) -> None:
    lines = req_file.read_text().splitlines()
    frozen: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            frozen.append(line)
            continue
        if ";" in stripped:
            req_part, marker = stripped.split(";", 1)
            marker = ";" + marker  # preserve original marker spacing
        else:
            req_part, marker = stripped, ""
        name = req_part.split("==")[0].strip()
        try:
            pkg_version = version(name)
            req_part = f"{name}=={pkg_version}"
        except PackageNotFoundError:
            req_part = req_part.strip()
        frozen.append(req_part + marker)
    req_file.write_text("\n".join(frozen) + "\n")


def main(argv: list[str] | None = None) -> None:
    path = Path(argv[0]) if argv else Path("requirements.txt")
    freeze(path)


if __name__ == "__main__":
    main(sys.argv[1:])
