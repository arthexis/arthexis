#!/usr/bin/env python3
"""Collect Arthexis release readiness evidence from GitHub and the checkout."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def default_checkout() -> Path:
    return Path(os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")).expanduser()


def run(cmd: list[str], cwd: Path | None = None) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    return {"cmd": cmd, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def gh_json(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip(), "returncode": proc.returncode}
    return json.loads(proc.stdout or "null")


def git(checkout: Path, args: list[str]) -> dict[str, Any]:
    return run(["git", "-C", str(checkout), *args])


def normalize_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def tag_remote_exists(version: str, repo: str | None, checkout: Path) -> dict[str, Any]:
    tag = normalize_tag(version)
    if repo and shutil.which("gh"):
        data = gh_json(["release", "view", tag, "--repo", repo, "--json", "tagName,url,isDraft,isPrerelease"])
        if isinstance(data, dict) and not data.get("error"):
            return {"exists": True, "source": "gh release", "data": data}
    remote = git(checkout, ["ls-remote", "--tags", "origin", tag])
    return {"exists": bool(remote["stdout"]), "source": "git ls-remote", "data": remote}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="arthexis/arthexis")
    parser.add_argument("--checkout", type=Path, default=default_checkout())
    parser.add_argument("--issue")
    parser.add_argument("--version")
    args = parser.parse_args()

    checkout = args.checkout.resolve()
    result: dict[str, Any] = {
        "repo": args.repo,
        "checkout": str(checkout),
        "ghAvailable": bool(shutil.which("gh")),
        "gitHead": git(checkout, ["rev-parse", "HEAD"]),
        "gitStatus": git(checkout, ["status", "--short"]),
        "gitBranch": git(checkout, ["branch", "--show-current"]),
        "latestRelease": None,
        "openPullRequests": None,
        "issue": None,
        "version": args.version,
        "tag": None,
    }
    if shutil.which("gh"):
        result["authStatus"] = run(["gh", "auth", "status"])
        result["latestRelease"] = gh_json(["release", "list", "--repo", args.repo, "--limit", "1", "--json", "tagName,name,isDraft,isPrerelease,publishedAt"])
        result["openPullRequests"] = gh_json(["pr", "list", "--repo", args.repo, "--state", "open", "--limit", "100", "--json", "number,title,isDraft,mergeStateStatus,reviewDecision,url"])
        if args.issue:
            result["issue"] = gh_json(["issue", "view", args.issue, "--repo", args.repo, "--json", "number,title,state,comments,url"])
    if args.version:
        result["tag"] = tag_remote_exists(args.version, args.repo, checkout)
        result["tag"]["tagName"] = normalize_tag(args.version)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
