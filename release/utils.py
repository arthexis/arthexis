from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ``toml`` is used to write a temporary ``pyproject.toml`` file during the
# release build.  The third party library may not be installed in the test
# environment, so we attempt to import it lazily and fall back to the standard
# library's ``json`` module for a very small substitute writer.
try:  # pragma: no cover - optional dependency
    import toml  # type: ignore
except Exception:  # pragma: no cover - fallback when missing
    toml = None  # type: ignore

from . import Credentials, Package, DEFAULT_PACKAGE
from config.offline import requires_network


class ReleaseError(Exception):
    pass


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check)


def _git_clean() -> bool:
    proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    return not proc.stdout.strip()


def _current_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()


def _write_pyproject(package: Package, version: str, requirements: list[str]) -> None:
    content = {
        "build-system": {
            "requires": ["setuptools", "wheel"],
            "build-backend": "setuptools.build_meta",
        },
        "project": {
            "name": package.name,
            "version": version,
            "description": package.description,
            "readme": {"file": "README.md", "content-type": "text/markdown"},
            "requires-python": package.python_requires,
            "license": package.license,
            "authors": [{"name": package.author, "email": package.email}],
            "classifiers": [
                "Programming Language :: Python :: 3",
                "Framework :: Django",
            ],
            "dependencies": requirements,
            "urls": {
                "Repository": package.repository_url,
                "Homepage": package.homepage_url,
            },
        },
        "tool": {
            "setuptools": {
                "packages": [
                    "accounts",
                    "config",
                    "nodes",
                    "ocpp",
                    "references",
                    "readme",
                    "website",
                    "release",
                    "crm",
                    "crm.odoo",
                ]
            }
        },
    }

    def _dump_toml(data: dict) -> str:
        if toml is not None and hasattr(toml, "dumps"):
            return toml.dumps(data)
        # Fallback: store as JSON; good enough for tests which do not
        # consume this file.  Using json keeps this function dependency free.
        import json

        return json.dumps(data)

    Path("pyproject.toml").write_text(_dump_toml(content), encoding="utf-8")


def _ensure_changelog() -> str:
    header = "Changelog\n=========\n\n"
    path = Path("CHANGELOG.rst")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.startswith("Changelog"):
        text = header + text
    if "Unreleased" not in text:
        text = text[: len(header)] + "Unreleased\n----------\n\n" + text[len(header):]
    return text


def _pop_unreleased(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    try:
        idx = lines.index("Unreleased")
    except ValueError:
        return "", text
    body = []
    i = idx + 2
    while i < len(lines) and lines[i].startswith("- "):
        body.append(lines[i])
        i += 1
    if i < len(lines) and lines[i] == "":
        i += 1
    new_lines = lines[:idx] + lines[i:]
    return "\n".join(body), "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


def _last_changelog_build() -> Optional[str]:
    path = Path("CHANGELOG.rst")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if "[build" in line:
            try:
                return line.split("[build", 1)[1].split("]", 1)[0].strip()
            except Exception:
                return None
    return None


def update_changelog(version: str, build_hash: str, prev_build: Optional[str] = None) -> None:
    text = _ensure_changelog()
    body, text = _pop_unreleased(text)
    if not body:
        prev_build = prev_build or _last_changelog_build()
        log_range = f"{prev_build}..HEAD" if prev_build else "HEAD"
        proc = subprocess.run(
            ["git", "log", "--pretty=%h %s", "--no-merges", log_range],
            capture_output=True,
            text=True,
            check=False,
        )
        body = "\n".join(f"- {l.strip()}" for l in proc.stdout.splitlines() if l.strip())
    header = f"{version} [build {build_hash}]"
    underline = "-" * len(header)
    entry = "\n".join([header, underline, "", body, ""]).rstrip() + "\n\n"
    base_header = "Changelog\n=========\n\n"
    remaining = text[len(base_header):]
    new_text = base_header + "Unreleased\n----------\n\n" + entry + remaining
    Path("CHANGELOG.rst").write_text(new_text, encoding="utf-8")


@requires_network
def build(
    *,
    bump: bool = False,
    dist: bool = False,
    twine: bool = False,
    git: bool = False,
    tag: bool = False,
    all: bool = False,
    force: bool = False,
    package: Package = DEFAULT_PACKAGE,
    creds: Optional[Credentials] = None,
) -> None:
    if all:
        bump = dist = twine = git = tag = True

    if git and not _git_clean():
        raise ReleaseError("Git repository is not clean")

    version_path = Path("VERSION")
    if not version_path.exists():
        raise ReleaseError("VERSION file not found")
    current_version = version_path.read_text().strip()
    if bump:
        major, minor, patch = map(int, current_version.split("."))
        patch += 1
        current_version = f"{major}.{minor}.{patch}"
        version_path.write_text(current_version + "\n")
    version = current_version

    requirements = [
        line.strip()
        for line in Path("requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    commit_hash = _current_commit()
    Path("BUILD").write_text(commit_hash + "\n")
    prev_build = _last_changelog_build()
    update_changelog(version, commit_hash, prev_build)

    _write_pyproject(package, version, requirements)

    if dist:
        if Path("dist").exists():
            for p in Path("dist").glob("*"):
                p.unlink()
            Path("dist").rmdir()
        _run([sys.executable, "-m", "build"])
        if twine:
            if not force:
                try:  # Lazy import so tests do not require requests
                    import requests  # type: ignore
                except Exception:  # pragma: no cover - requests optional
                    requests = None  # type: ignore
                if requests is not None:
                    resp = requests.get(
                        f"https://pypi.org/pypi/{package.name}/json"
                    )
                    if resp.ok:
                        releases = resp.json().get("releases", {})
                        if version in releases:
                            raise ReleaseError(
                                f"Version {version} already on PyPI"
                            )
            token = os.environ.get("PYPI_API_TOKEN") if creds is None else creds.token
            user = os.environ.get("PYPI_USERNAME") if creds is None else creds.username
            pwd = os.environ.get("PYPI_PASSWORD") if creds is None else creds.password
            cmd = [sys.executable, "-m", "twine", "upload", "dist/*"]
            if token:
                cmd += ["--username", "__token__", "--password", token]
            elif user and pwd:
                cmd += ["--username", user, "--password", pwd]
            else:
                raise ReleaseError("Missing PyPI credentials")
            _run(cmd)

    if git:
        files = ["VERSION", "BUILD", "pyproject.toml", "CHANGELOG.rst"]
        _run(["git", "add"] + files)
        msg = f"PyPI Release v{version}" if twine else f"Release v{version}"
        _run(["git", "commit", "-m", msg])
        _run(["git", "push"])
    if tag:
        tag_name = f"v{version}"
        _run(["git", "tag", tag_name])
        _run(["git", "push", "origin", tag_name])
