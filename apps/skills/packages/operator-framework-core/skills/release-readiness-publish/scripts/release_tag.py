#!/usr/bin/env python3
"""Validate, create, and optionally push an Arthexis release tag."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?$")


def default_checkout() -> Path:
    return Path(os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")).expanduser()


def run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    return {"cmd": cmd, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def add_check(output: dict[str, Any], name: str, ok: bool, detail: str = "") -> None:
    check: dict[str, Any] = {"ok": ok, "name": name}
    if detail and not ok:
        check["detail"] = detail
    output["checks"].append(check)


def collect_checks(checkout: Path, version: str, args: argparse.Namespace) -> dict[str, Any]:
    output: dict[str, Any] = {"checkout": str(checkout), "version": version, "write": args.write, "push": args.push, "checks": []}
    add_check(output, "version-format", bool(VERSION_RE.match(version)), "expected vX.Y.Z")

    status = run(["git", "status", "--short"], checkout)
    output["status"] = status
    add_check(
        output,
        "clean-checkout",
        not bool(status["stdout"] and not args.allow_dirty),
        "dirty checkout requires --allow-dirty",
    )

    local_tag = run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{version}"], checkout)
    remote_tag = run(["git", "ls-remote", "--tags", args.remote, version], checkout)
    output["localTag"] = local_tag
    output["remoteTag"] = remote_tag
    add_check(
        output,
        "tag-absent",
        remote_tag["returncode"] == 0 and not (local_tag["returncode"] == 0 or remote_tag["stdout"]),
        "remote tag probe failed" if remote_tag["returncode"] != 0 else "tag already exists",
    )
    return output


def write_release_tag(output: dict[str, Any], checkout: Path, args: argparse.Namespace) -> None:
    version = str(output["version"])
    tag_cmd = ["git", "tag", "-a", version, "-m", args.message or f"Release {version}"]
    push_cmd = ["git", "push", args.remote, version]
    output["plannedCommands"] = [tag_cmd] + ([push_cmd] if args.push else [])

    if not args.write or not all(item["ok"] for item in output["checks"]):
        return
    output["tagResult"] = run(tag_cmd, checkout)
    if output["tagResult"]["returncode"] == 0 and args.push:
        output["pushResult"] = run(push_cmd, checkout)


def write_exit_code(output: dict[str, Any], write: bool) -> int:
    if not all(item["ok"] for item in output["checks"]):
        return 1
    if write:
        return int(output.get("pushResult", output.get("tagResult", {"returncode": 0})).get("returncode") or 0)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=default_checkout())
    parser.add_argument("--version", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--message")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    checkout = args.checkout.resolve()
    version = args.version if args.version.startswith("v") else f"v{args.version}"
    output = collect_checks(checkout, version, args)
    write_release_tag(output, checkout, args)
    print(json.dumps(output, indent=2))
    return write_exit_code(output, args.write)


if __name__ == "__main__":
    raise SystemExit(main())
