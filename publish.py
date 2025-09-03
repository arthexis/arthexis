#!/usr/bin/env python3
"""Build and publish the project to PyPI.

Usage:
    python publish.py <version> [--repository-url URL]

The script checks whether the given version already exists on PyPI.
If the version is new, it removes the ``dist`` directory, builds the
package using ``python -m build`` and uploads the result with Twine.

The script is designed to run on both Windows and Linux.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

PACKAGE_NAME = "arthexis"


def version_exists(version: str) -> bool:
    """Return True if version already exists on PyPI."""
    url = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.load(response)
        return version in data.get("releases", {})
    except Exception:
        # On network failures assume the version does not exist.
        return False


def build() -> None:
    """Remove existing build artifacts and create a fresh build."""
    if os.path.isdir("dist"):
        shutil.rmtree("dist")
    subprocess.run([sys.executable, "-m", "build"], check=True)


def publish(repository_url: str | None) -> None:
    """Upload built distributions to PyPI using Twine."""
    cmd = [sys.executable, "-m", "twine", "upload", "dist/*"]
    if repository_url:
        cmd.extend(["--repository-url", repository_url])
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and publish package to PyPI")
    parser.add_argument("version", help="Version to publish")
    parser.add_argument(
        "--repository-url",
        help="Optional repository URL for Twine",
        default=None,
    )
    args = parser.parse_args(argv)

    if version_exists(args.version):
        print(f"Version {args.version} already exists on PyPI")
        return 1

    build()
    publish(args.repository_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
