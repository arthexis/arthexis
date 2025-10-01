from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

try:  # pragma: no cover - optional dependency
    import toml  # type: ignore
except Exception:  # pragma: no cover - fallback when missing
    toml = None  # type: ignore

from config.offline import requires_network, network_available


@dataclass
class Package:
    """Metadata for building a distributable package."""

    name: str
    description: str
    author: str
    email: str
    python_requires: str
    license: str
    repository_url: str = "https://github.com/arthexis/arthexis"
    homepage_url: str = "https://arthexis.com"
    version_path: Optional[Path | str] = None
    dependencies_path: Optional[Path | str] = None
    test_command: Optional[str] = None


@dataclass
class Credentials:
    """Credentials for uploading to PyPI."""

    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    def twine_args(self) -> list[str]:
        if self.token:
            return ["--username", "__token__", "--password", self.token]
        if self.username and self.password:
            return ["--username", self.username, "--password", self.password]
        raise ValueError("Missing PyPI credentials")


DEFAULT_PACKAGE = Package(
    name="arthexis",
    description="Django-based MESH system",
    author="Rafael J. Guillén-Osorio",
    email="tecnologia@gelectriic.com",
    python_requires=">=3.10",
    license="GPL-3.0-only",
)


class ReleaseError(Exception):
    pass


class TestsFailed(ReleaseError):
    """Raised when the test suite fails.

    Attributes:
        log_path: Location of the saved test log.
        output:   Combined stdout/stderr from the test run.
    """

    def __init__(self, log_path: Path, output: str):
        super().__init__("Tests failed")
        self.log_path = log_path
        self.output = output


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check)


def _git_clean() -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    )
    return not proc.stdout.strip()


def _git_has_staged_changes() -> bool:
    """Return True if there are staged changes ready to commit."""
    proc = subprocess.run(["git", "diff", "--cached", "--quiet"])
    return proc.returncode != 0


def _manager_credentials() -> Optional[Credentials]:
    """Return credentials from the Package's release manager if available."""
    try:  # pragma: no cover - optional dependency
        from core.models import Package as PackageModel

        package_obj = PackageModel.objects.select_related("release_manager").first()
        if package_obj and package_obj.release_manager:
            return package_obj.release_manager.to_credentials()
    except Exception:
        return None
    return None


def run_tests(
    log_path: Optional[Path] = None,
    command: Optional[Sequence[str]] = None,
) -> subprocess.CompletedProcess:
    """Run the project's test suite and write output to ``log_path``.

    The log file is stored separately from regular application logs to avoid
    mixing test output with runtime logging.
    """

    log_path = log_path or Path("logs/test.log")
    cmd = list(command) if command is not None else [sys.executable, "manage.py", "test"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return proc


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
                    "core",
                    "config",
                    "nodes",
                    "ocpp",
                    "pages",
                ]
            }
        },
    }

    def _dump_toml(data: dict) -> str:
        if toml is not None and hasattr(toml, "dumps"):
            return toml.dumps(data)
        import json

        return json.dumps(data)

    Path("pyproject.toml").write_text(_dump_toml(content), encoding="utf-8")


@requires_network
def build(
    *,
    version: Optional[str] = None,
    bump: bool = False,
    tests: bool = False,
    dist: bool = False,
    twine: bool = False,
    git: bool = False,
    tag: bool = False,
    all: bool = False,
    force: bool = False,
    package: Package = DEFAULT_PACKAGE,
    creds: Optional[Credentials] = None,
    stash: bool = False,
) -> None:
    if all:
        bump = dist = twine = git = tag = True

    stashed = False
    if not _git_clean():
        if stash:
            _run(["git", "stash", "--include-untracked"])
            stashed = True
        else:
            raise ReleaseError(
                "Git repository is not clean. Commit, stash, or enable auto stash before building."
            )

    version_path = Path(package.version_path) if package.version_path else Path("VERSION")
    if version is None:
        if not version_path.exists():
            raise ReleaseError("VERSION file not found")
        version = version_path.read_text().strip()
        if bump:
            major, minor, patch = map(int, version.split("."))
            patch += 1
            version = f"{major}.{minor}.{patch}"
            version_path.write_text(version + "\n")
    else:
        # Ensure the VERSION file reflects the provided release version
        if version_path.parent != Path("."):
            version_path.parent.mkdir(parents=True, exist_ok=True)
        version_path.write_text(version + "\n")

    requirements_path = (
        Path(package.dependencies_path)
        if package.dependencies_path
        else Path("requirements.txt")
    )
    requirements = [
        line.strip()
        for line in requirements_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if tests:
        log_path = Path("logs/test.log")
        test_command = (
            shlex.split(package.test_command)
            if package.test_command
            else None
        )
        proc = run_tests(log_path=log_path, command=test_command)
        if proc.returncode != 0:
            raise TestsFailed(log_path, proc.stdout + proc.stderr)

    _write_pyproject(package, version, requirements)
    if dist:
        if Path("dist").exists():
            shutil.rmtree("dist")
        try:
            import build  # type: ignore
        except Exception:
            _run([sys.executable, "-m", "pip", "install", "build"])
        _run([sys.executable, "-m", "build"])

    if git:
        files = ["VERSION", "pyproject.toml"]
        _run(["git", "add"] + files)
        msg = f"PyPI Release v{version}" if twine else f"Release v{version}"
        if _git_has_staged_changes():
            _run(["git", "commit", "-m", msg])
        _run(["git", "push"])

    if tag:
        tag_name = f"v{version}"
        _run(["git", "tag", tag_name])
        _run(["git", "push", "origin", tag_name])

    if dist and twine:
        if not force:
            try:  # pragma: no cover - requests optional
                import requests  # type: ignore
            except Exception:
                requests = None  # type: ignore
            if requests is not None:
                resp = requests.get(f"https://pypi.org/pypi/{package.name}/json")
                if resp.ok:
                    releases = resp.json().get("releases", {})
                    if version in releases:
                        raise ReleaseError(f"Version {version} already on PyPI")
        creds = (
            creds
            or _manager_credentials()
            or Credentials(
                token=os.environ.get("PYPI_API_TOKEN"),
                username=os.environ.get("PYPI_USERNAME"),
                password=os.environ.get("PYPI_PASSWORD"),
            )
        )
        files = sorted(str(p) for p in Path("dist").glob("*"))
        if not files:
            raise ReleaseError("dist directory is empty")
        cmd = [sys.executable, "-m", "twine", "upload", *files]
        try:
            cmd += creds.twine_args()
        except ValueError:
            raise ReleaseError("Missing PyPI credentials")
        _run(cmd)

    if stashed:
        _run(["git", "stash", "pop"], check=False)


def promote(
    *,
    package: Package = DEFAULT_PACKAGE,
    version: str,
    creds: Optional[Credentials] = None,
) -> None:
    """Build the package and commit the release on the current branch."""
    if not _git_clean():
        raise ReleaseError("Git repository is not clean")
    build(
        package=package,
        version=version,
        creds=creds,
        tests=False,
        dist=True,
        git=False,
        tag=False,
        stash=False,
    )
    _run(["git", "add", "."])  # add all changes
    if _git_has_staged_changes():
        _run(["git", "commit", "-m", f"Release v{version}"])


def publish(
    *,
    package: Package = DEFAULT_PACKAGE,
    version: str,
    creds: Optional[Credentials] = None,
) -> None:
    """Upload the existing distribution to PyPI."""
    if network_available():
        try:  # pragma: no cover - requests optional
            import requests  # type: ignore
        except Exception:
            requests = None  # type: ignore
        if requests is not None:
            resp = requests.get(f"https://pypi.org/pypi/{package.name}/json")
            if resp.ok and version in resp.json().get("releases", {}):
                raise ReleaseError(f"Version {version} already on PyPI")
    if not Path("dist").exists():
        raise ReleaseError("dist directory not found")
    creds = (
        creds
        or _manager_credentials()
        or Credentials(
            token=os.environ.get("PYPI_API_TOKEN"),
            username=os.environ.get("PYPI_USERNAME"),
            password=os.environ.get("PYPI_PASSWORD"),
        )
    )
    files = sorted(str(p) for p in Path("dist").glob("*"))
    if not files:
        raise ReleaseError("dist directory is empty")
    cmd = [sys.executable, "-m", "twine", "upload", *files]
    try:
        cmd += creds.twine_args()
    except ValueError:
        raise ReleaseError("Missing PyPI credentials")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ReleaseError(proc.stdout + proc.stderr)

    tag_name = f"v{version}"
    _run(["git", "tag", tag_name])
    _run(["git", "push", "origin", tag_name])
