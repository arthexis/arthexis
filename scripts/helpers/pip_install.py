#!/usr/bin/env python3
"""Run ``pip install`` with compact output for satisfied requirements."""

from __future__ import annotations

import subprocess
import sys
from typing import Iterable, Set


ALLOWED_BUILD_FAILURES = {"spidev", "RPi.GPIO"}


def _extract_failed_builds(line: str) -> Set[str]:
    failures: Set[str] = set()
    marker = "Failed to build "
    if marker in line:
        failures.update(line.split(marker, 1)[1].split())
        return failures

    wheel_marker = "ERROR: Failed building wheel for "
    if wheel_marker in line:
        failures.add(line.split(wheel_marker, 1)[1].strip())
        return failures

    trimmed = line.strip()
    if trimmed.startswith("╰─>") or trimmed.startswith("->"):
        names = trimmed.split(">", 1)[1]
        for name in names.split(","):
            cleaned = name.strip()
            if cleaned:
                failures.add(cleaned)
    return failures


def _iter_pip_output(cmd: Iterable[str]) -> int:
    """Stream pip output while replacing satisfied requirement lines with dots."""
    process = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert process.stdout is not None

    printed_dot = False
    failed_builds: Set[str] = set()
    non_allowed_failure = False
    missing_compiler = False
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

            new_failures = _extract_failed_builds(line)
            if new_failures:
                failed_builds.update(new_failures)
                if not new_failures.issubset(ALLOWED_BUILD_FAILURES):
                    non_allowed_failure = True
            if "ERROR:" in line and "Failed building wheel for" not in line and "Failed to build " not in line:
                non_allowed_failure = True
            if "No such file or directory" in line and "gcc" in line:
                missing_compiler = True

            sys.stdout.write(raw_line)
        return_code = process.wait()
    finally:
        if printed_dot:
            sys.stdout.write("\n")
            sys.stdout.flush()

    if return_code != 0 and failed_builds and not non_allowed_failure:
        if failed_builds.issubset(ALLOWED_BUILD_FAILURES):
            sys.stderr.write(
                "Optional hardware dependencies failed to build "
                f"({', '.join(sorted(failed_builds))}); continuing install.\n"
            )
            if missing_compiler:
                sys.stderr.write(
                    "Install build tools (e.g. `sudo apt-get install build-essential`) "
                    "if you need GPIO/SPIDEV support.\n"
                )
            return 0

    return return_code


def main() -> int:
    pip_args = sys.argv[1:]
    cmd = [sys.executable, "-m", "pip", "install", *pip_args]
    return _iter_pip_output(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
