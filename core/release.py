from __future__ import annotations

import os
import subprocess
import sys
import shutil
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    license="MIT",
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
    proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    return not proc.stdout.strip()


def _current_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()


def _current_branch() -> str:
    return (
        subprocess.check_output([
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ]).decode().strip()
    )


def _normalize_initial_migration() -> None:
    """Ensure the initial migration is not marked as squashed.

    Django's ``squashmigrations`` command produces a file named
    ``0001_squashed_0001_initial.py`` with a ``replaces`` declaration. If that
    file exists, or if ``0001_initial.py`` contains a ``replaces`` line from a
    previous squashing, convert it into a normal initial migration so that
    future squashing runs cleanly.
    """

    migrations_dir = Path("core/migrations")
    squashed = migrations_dir / "0001_squashed_0001_initial.py"
    initial = migrations_dir / "0001_initial.py"

    if squashed.exists():
        text = squashed.read_text(encoding="utf-8")
        text = re.sub(r"^\s*replaces = .*$\n", "", text, flags=re.MULTILINE)
        initial.write_text(text, encoding="utf-8")
        squashed.unlink()
    elif initial.exists():
        text = initial.read_text(encoding="utf-8")
        if "replaces" in text:
            text = re.sub(r"^\s*replaces = .*$\n", "", text, flags=re.MULTILINE)
            initial.write_text(text, encoding="utf-8")

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


def run_tests(log_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run the project's test suite and write output to ``log_path``.

    The log file is stored separately from regular application logs to avoid
    mixing test output with runtime logging.
    """

    log_path = log_path or Path("logs/test.log")
    proc = subprocess.run(
        [sys.executable, "manage.py", "test"],
        capture_output=True,
        text=True,
    )
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


def _last_changelog_revision() -> Optional[str]:
    path = Path("CHANGELOG.rst")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if "[revision" in line:
            try:
                return line.split("[revision", 1)[1].split("]", 1)[0].strip()
            except Exception:
                return None
    return None


def update_changelog(version: str, revision: str, prev_revision: Optional[str] = None) -> None:
    text = _ensure_changelog()
    body, text = _pop_unreleased(text)
    if not body:
        prev_revision = prev_revision or _last_changelog_revision()
        log_range = f"{prev_revision}..HEAD" if prev_revision else "HEAD"
        proc = subprocess.run(
            ["git", "log", "--pretty=%h %s", "--no-merges", log_range],
            capture_output=True,
            text=True,
            check=False,
        )
        body = "\n".join(
            f"- {l.strip()}" for l in proc.stdout.splitlines() if l.strip()
        )
    header = f"{version} [revision {revision}]"
    underline = "-" * len(header)
    entry = "\n".join([header, underline, "", body, ""]).rstrip() + "\n\n"
    base_header = "Changelog\n=========\n\n"
    remaining = text[len(base_header):]
    new_text = base_header + "Unreleased\n----------\n\n" + entry + remaining
    Path("CHANGELOG.rst").write_text(new_text, encoding="utf-8")


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

    version_path = Path("VERSION")
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
        version_path.write_text(version + "\n")

    requirements = [
        line.strip()
        for line in Path("requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if tests:
        log_path = Path("logs/test.log")
        proc = run_tests(log_path=log_path)
        if proc.returncode != 0:
            raise TestsFailed(log_path, proc.stdout + proc.stderr)

    commit_hash = _current_commit()
    prev_revision = _last_changelog_revision()
    update_changelog(version, commit_hash, prev_revision)

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
        files = ["VERSION", "pyproject.toml", "CHANGELOG.rst"]
        _run(["git", "add"] + files)
        msg = f"PyPI Release v{version}" if twine else f"Release v{version}"
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
                resp = requests.get(
                    f"https://pypi.org/pypi/{package.name}/json"
                )
                if resp.ok:
                    releases = resp.json().get("releases", {})
                    if version in releases:
                        raise ReleaseError(
                            f"Version {version} already on PyPI"
                        )
        creds = creds or _manager_credentials() or Credentials(
            token=os.environ.get("PYPI_API_TOKEN"),
            username=os.environ.get("PYPI_USERNAME"),
            password=os.environ.get("PYPI_PASSWORD"),
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
) -> tuple[str, str, str]:
    """Create a release branch and build the package without tests.

    Returns a tuple of the release commit hash, the new branch name and the
    original branch name.
    """
    current = _current_branch()
    tmp_branch = f"release/{version}"
    if not _git_clean():
        raise ReleaseError("Git repository is not clean")
    try:
        try:
            _run(["git", "checkout", "-b", tmp_branch])
        except subprocess.CalledProcessError:
            _run(["git", "checkout", tmp_branch])
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
        _normalize_initial_migration()
        try:  # best effort
            _run(
                [
                    sys.executable,
                    "manage.py",
                    "squashmigrations",
                    "core",
                    "0001",
                    "--noinput",
                    "--skip-checks",
                ],
                check=False,
            )
        except Exception:
            # The squashmigrations command may not be available or could fail
            # (e.g. when no migrations exist). Any errors should not interrupt
            # the release promotion flow.
            pass
        _normalize_initial_migration()
        _run(["git", "add", "."])  # add all changes
        _run(["git", "commit", "-m", f"Release v{version}"])
        commit_hash = _current_commit()
        release_name = f"{package.name}-{version}-{commit_hash[:7]}"
        branch = f"release-{release_name}"
        _run(["git", "branch", "-m", branch])
    except Exception:
        _run(["git", "checkout", current])
        raise
    return commit_hash, branch, current


def publish(
    *, package: Package = DEFAULT_PACKAGE, version: str, creds: Optional[Credentials] = None
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
    creds = creds or _manager_credentials() or Credentials(
        token=os.environ.get("PYPI_API_TOKEN"),
        username=os.environ.get("PYPI_USERNAME"),
        password=os.environ.get("PYPI_PASSWORD"),
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
