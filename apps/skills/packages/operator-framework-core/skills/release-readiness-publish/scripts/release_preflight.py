#!/usr/bin/env python3
"""Summarize the next valid Arthexis GitHub release action."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

SEMVER_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


def default_checkout() -> Path:
    return Path(
        os.environ.get("ARTHEXIS_REPO", Path.home() / "Repos" / "arthexis")
    ).expanduser()


def run(cmd: list[str], cwd: Path | None = None) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def git(checkout: Path, args: list[str]) -> dict[str, Any]:
    safe_directory = checkout.as_posix()
    return run(["git", "-c", f"safe.directory={safe_directory}", "-C", str(checkout), *args])


def gh_json(args: list[str]) -> Any:
    if not shutil.which("gh"):
        return {"error": "gh not found", "returncode": 127}
    proc = subprocess.run(
        ["gh", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip(), "returncode": proc.returncode}
    return json.loads(proc.stdout or "null")


def read_version(checkout: Path) -> str:
    return (checkout / "VERSION").read_text(encoding="utf-8").strip()


def next_patch(version: str) -> str | None:
    match = SEMVER_RE.match(version)
    if not match:
        return None
    parts = {key: int(value) for key, value in match.groupdict().items()}
    return f"{parts['major']}.{parts['minor']}.{parts['patch'] + 1}"


def pypi_release_exists(package: str, version: str) -> dict[str, Any]:
    release_url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        with urlopen(release_url, timeout=15) as response:
            return {
                "exists": response.status == 200,
                "url": release_url,
                "source": "release",
            }
    except HTTPError as exc:
        if exc.code != 404:
            return {"exists": None, "url": release_url, "error": f"HTTP {exc.code}"}
    except URLError as exc:
        return {"exists": None, "url": release_url, "error": str(exc.reason)}

    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return {"exists": False, "url": url, "error": "package not found"}
        return {"exists": None, "url": url, "error": f"HTTP {exc.code}"}
    except URLError as exc:
        return {"exists": None, "url": url, "error": str(exc.reason)}
    releases = payload.get("releases", {})
    return {"exists": version in releases, "url": url, "source": "project"}


def latest_release(repo: str) -> Any:
    return gh_json(
        [
            "release",
            "list",
            "--repo",
            repo,
            "--limit",
            "1",
            "--json",
            "tagName,name,isDraft,isPrerelease,publishedAt,isLatest",
        ]
    )


def release_for_tag(repo: str, tag: str) -> Any:
    return gh_json(
        [
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "tagName,url,isDraft,isPrerelease,publishedAt,assets",
        ]
    )


def open_prs(repo: str) -> Any:
    return gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,isDraft,mergeStateStatus,reviewDecision,url",
        ]
    )


def readiness_issue(repo: str) -> Any:
    return gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            "Release Readiness Report in:title",
            "--json",
            "number,title,body,url,updatedAt",
        ]
    )


def evidence_error(value: Any, *, allow_not_found: bool = False) -> str:
    if not isinstance(value, dict) or not value.get("error"):
        return ""
    error = str(value["error"])
    lower_error = error.lower()
    if allow_not_found and ("release not found" in lower_error or "could not resolve to a release" in lower_error):
        return ""
    return error


def probe_failed(value: Any) -> str:
    if not isinstance(value, dict):
        return "missing probe result"
    if int(value.get("returncode") or 0) == 0:
        return ""
    return str(value.get("stderr") or value.get("stdout") or f"returncode {value.get('returncode')}")


def decide(result: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    actions: list[str] = []
    git_evidence_failed = False

    fetch_error = probe_failed(result.get("fetch", {"returncode": 0}))
    if fetch_error:
        blockers.append(f"git fetch probe failed: {fetch_error}")
        git_evidence_failed = True

    for key in ("status", "head", "originMain", "remoteTag"):
        error = probe_failed(result["git"].get(key))
        if error:
            blockers.append(f"git {key} probe failed: {error}")
            git_evidence_failed = True

    if not git_evidence_failed and result["git"]["status"].get("stdout"):
        blockers.append("checkout is dirty")
    if (
        not git_evidence_failed
        and result["git"]["head"].get("stdout") != result["git"]["originMain"].get("stdout")
    ):
        blockers.append("local main is not at origin/main")

    for key in ("latestRelease", "openPullRequests", "readinessIssue"):
        error = evidence_error(result.get(key))
        if error:
            blockers.append(f"{key} lookup failed: {error}")
        elif not isinstance(result.get(key), list):
            blockers.append(f"{key} lookup did not return expected list evidence")
    release_error = evidence_error(result.get("releaseForVersion"), allow_not_found=True)
    if release_error:
        blockers.append(f"releaseForVersion lookup failed: {release_error}")

    prs = result.get("openPullRequests")
    if isinstance(prs, list) and prs:
        blockers.append(f"{len(prs)} open pull request(s)")

    version = result["version"]
    tag = f"v{version}"
    release = result.get("releaseForVersion")
    pypi = result.get("pypi", {})
    tag_exists = bool(result["git"]["remoteTag"].get("stdout"))
    release_exists = isinstance(release, dict) and not release.get("error")
    pypi_exists = pypi.get("exists") is True
    pypi_missing = pypi.get("exists") is None
    if pypi.get("exists") is None:
        blockers.append(f"PyPI lookup failed: {pypi.get('error', 'unknown error')}")

    if git_evidence_failed or pypi_missing:
        pass
    elif release_exists and pypi_exists:
        candidate = result.get("nextPatchVersion")
        actions.append(
            f"{tag} is already published; bump VERSION to {candidate} before the next release."
        )
    elif tag_exists and not release_exists:
        actions.append(f"Dispatch publish.yml for existing tag {tag}.")
    elif not tag_exists and not pypi_exists and not pypi_missing:
        actions.append(
            f"Push a reviewed VERSION={version} commit to main; tag-from-version.yml will create {tag} and dispatch publish.yml."
        )

    return {
        "blocked": bool(blockers),
        "blockers": blockers,
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="arthexis/arthexis")
    parser.add_argument("--checkout", type=Path, default=default_checkout())
    parser.add_argument("--package", default="arthexis")
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()

    checkout = args.checkout.resolve()
    if args.fetch:
        fetch = git(checkout, ["fetch", "--tags", "origin"])
    else:
        fetch = {"skipped": True}

    version = read_version(checkout)
    tag = f"v{version}"
    result: dict[str, Any] = {
        "repo": args.repo,
        "checkout": str(checkout),
        "version": version,
        "tag": tag,
        "nextPatchVersion": next_patch(version),
        "fetch": fetch,
        "git": {
            "head": git(checkout, ["rev-parse", "HEAD"]),
            "originMain": git(checkout, ["rev-parse", "origin/main"]),
            "status": git(checkout, ["status", "--short"]),
            "branch": git(checkout, ["branch", "--show-current"]),
            "remoteTag": git(checkout, ["ls-remote", "--tags", "origin", tag]),
        },
        "latestRelease": latest_release(args.repo),
        "releaseForVersion": release_for_tag(args.repo, tag),
        "openPullRequests": open_prs(args.repo),
        "readinessIssue": readiness_issue(args.repo),
        "pypi": pypi_release_exists(args.package, version),
    }
    result["decision"] = decide(result)

    print(json.dumps(result, indent=2))
    return 1 if result["decision"]["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
