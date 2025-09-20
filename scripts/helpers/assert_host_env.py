#!/usr/bin/env python3
"""Ensure diagnostics run directly on the host and not inside containers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


TRUTHY_VALUES = {"1", "true", "yes", "on"}
OVERRIDE_ENV_VAR = "ARTHEXIS_ALLOW_CONTAINERIZED_DIAGNOSTICS"


def _gather_container_markers() -> list[str]:
    """Collect evidence that the current process is running in a container."""

    markers: list[str] = []

    if Path("/.dockerenv").exists():
        markers.append("/.dockerenv present")

    if Path("/run/.containerenv").exists():
        markers.append("/run/.containerenv present")

    container_env = os.environ.get("container")
    if container_env:
        markers.append(f"container environment variable set to '{container_env}'")

    for cgroup_path in (Path("/proc/1/cgroup"), Path("/proc/self/cgroup")):
        try:
            contents = cgroup_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in ("docker", "kubepods", "containerd", "podman", "lxc"):
            if token in contents:
                markers.append(f"{cgroup_path} references '{token}'")
                break

    return markers


def _override_enabled() -> bool:
    value = os.environ.get(OVERRIDE_ENV_VAR, "")
    return value.lower() in TRUTHY_VALUES


def main() -> int:
    if _override_enabled():
        print(
            f"{OVERRIDE_ENV_VAR} is set; skipping host environment assertion.",
            file=sys.stderr,
        )
        return 0

    markers = _gather_container_markers()
    if markers:
        print("Container runtime detected. Diagnostics must run on the host.")
        print("Detected markers:")
        for marker in markers:
            print(f"  - {marker}")
        print(
            "Set {}=1 to intentionally allow containerized diagnostics.".format(
                OVERRIDE_ENV_VAR
            )
        )
        return 1

    print("Host environment verified: no container markers detected.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
