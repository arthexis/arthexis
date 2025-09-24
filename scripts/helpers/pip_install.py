#!/usr/bin/env python3
"""Run ``pip install`` with compact output for satisfied requirements."""

from __future__ import annotations

import subprocess
import sys
from typing import Iterable


def _iter_pip_output(cmd: Iterable[str]) -> int:
    """Stream pip output while replacing satisfied requirement lines with dots."""
    process = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert process.stdout is not None

    printed_dot = False
    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if "Requirement already satisfied" in line:
                if not printed_dot:
                    sys.stdout.write("Skipping requirements without updates\n")
                    sys.stdout.flush()
                sys.stdout.write(".")
                sys.stdout.flush()
                printed_dot = True
                continue

            if printed_dot:
                sys.stdout.write("\n")
                printed_dot = False

            sys.stdout.write(raw_line)
        return_code = process.wait()
    finally:
        if printed_dot:
            sys.stdout.write("\n")
            sys.stdout.flush()

    return return_code


def main() -> int:
    pip_args = sys.argv[1:]
    cmd = [sys.executable, "-m", "pip", "install", *pip_args]
    return _iter_pip_output(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
