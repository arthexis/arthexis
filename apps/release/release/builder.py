from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from .defaults import DEFAULT_PACKAGE
from .models import Credentials, Package, ReleaseError
from .network import requires_network

try:  # pragma: no cover - optional dependency
    import toml  # type: ignore
except Exception:  # pragma: no cover - fallback when missing
    toml = None  # type: ignore


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


def _run(
    cmd: list[str],
    check: bool = True,
    *,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, cwd=cwd)


def _export_tracked_files(base_dir: Path, destination: Path) -> None:
    """Copy tracked files into ``destination`` preserving modifications."""

    def _copy_working_tree() -> None:
        for path in base_dir.rglob("*"):
            if any(part == ".git" for part in path.parts):
                continue
            relative = path.relative_to(base_dir)
            target_path = destination / relative
            if path.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_path)

    if not _is_git_repository(base_dir):
        _copy_working_tree()
        return

    with contextlib.suppress(subprocess.CalledProcessError, FileNotFoundError):
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            check=True,
            cwd=base_dir,
        )
        for entry in proc.stdout.split(b"\0"):
            if not entry:
                continue
            relative = Path(entry.decode("utf-8"))
            source_path = base_dir / relative
            if not source_path.exists():
                continue
            target_path = destination / relative
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        return

    _copy_working_tree()


def _build_in_sanitized_tree(base_dir: Path, *, generate_wheels: bool) -> None:
    """Run ``python -m build`` from a staging tree containing tracked files."""

    with tempfile.TemporaryDirectory(prefix="arthexis-build-") as temp_dir:
        staging_root = Path(temp_dir)
        _export_tracked_files(base_dir, staging_root)
        with _temporary_working_directory(staging_root):
            build_cmd = [sys.executable, "-m", "build", "--sdist"]
            if generate_wheels:
                build_cmd.append("--wheel")
            _run(build_cmd)
        built_dist = staging_root / "dist"
        if not built_dist.exists():
            raise ReleaseError("dist directory not created")
        destination_dist = base_dir / "dist"
        if destination_dist.exists():
            shutil.rmtree(destination_dist)
        shutil.copytree(built_dist, destination_dist)


@contextlib.contextmanager
def _temporary_working_directory(path: Path) -> "contextlib.AbstractContextManager[None]":
    current_dir = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(current_dir)


def _is_git_repository(base_dir: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=base_dir,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def _ignored_working_tree_paths(base_dir: Path) -> set[Path]:
    """Return paths that should not mark the repository as dirty.

    The release workflow writes runtime logs (``ARTHEXIS_LOG_DIR`` defaults to
    ``logs``) and lock files (``.locks``) into the working tree. Those
    artifacts are not part of source control and should not block a release.
    """

    ignored: set[Path] = set()
    base_dir = base_dir.resolve()

    env_log_dir = os.environ.get("ARTHEXIS_LOG_DIR")
    if env_log_dir:
        try:
            log_dir = Path(env_log_dir).expanduser().resolve()
        except OSError:
            log_dir = None
        else:
            try:
                log_dir.relative_to(base_dir)
            except ValueError:
                pass
            else:
                ignored.add(log_dir)

    for path in (base_dir / "logs", base_dir / ".locks"):
        ignored.add(path.resolve())

    return ignored


def _has_porcelain_changes(output: str, *, base_dir: Path | None = None) -> bool:
    """Return True when porcelain output includes working tree changes.

    ``git status --porcelain`` can include a leading branch summary line (``##``)
    when configuration such as ``status.branch`` is enabled. Being ahead or
    behind the remote should not mark the repository as dirty, so those summary
    lines are ignored. Untracked log and lock artifacts are also ignored so the
    release workflow does not fail on its own runtime files.
    """

    base_dir = (base_dir or Path.cwd()).resolve()
    ignored_paths = _ignored_working_tree_paths(base_dir)

    for line in output.splitlines():
        if not line or line.startswith("##"):
            continue

        entry = line[3:].split(" -> ", 1)[-1].strip()
        try:
            entry_path = (base_dir / entry).resolve()
        except Exception:
            return True

        if any(
            entry_path == ignored or entry_path.is_relative_to(ignored)
            for ignored in ignored_paths
        ):
            continue

        return True
    return False


def _git_clean() -> bool:
    if not _is_git_repository(Path.cwd()):
        return True

    proc = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    )
    return not _has_porcelain_changes(proc.stdout, base_dir=Path.cwd())


def _git_has_staged_changes() -> bool:
    """Return True if there are staged changes ready to commit."""
    proc = subprocess.run(["git", "diff", "--cached", "--quiet"])
    return proc.returncode != 0


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
    setuptools_config = {
        "packages": {"find": {"where": ["."]}},
        "include-package-data": True,
        "package-data": {pkg: ["**/*"] for pkg in package.packages},
    }

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
        "tool": {"setuptools": setuptools_config},
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
    from .network import fetch_pypi_releases
    from .uploader import upload_with_retries

    if all:
        dist = twine = git = tag = True

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
        test_command = shlex.split(package.test_command) if package.test_command else None
        proc = run_tests(log_path=log_path, command=test_command)
        if proc.returncode != 0:
            raise TestsFailed(log_path, proc.stdout + proc.stderr)

    _write_pyproject(package, version, requirements)
    if dist:
        if Path("dist").exists():
            shutil.rmtree("dist")
        build_dir = Path("build")
        if build_dir.exists():
            shutil.rmtree(build_dir)
        sys.modules.pop("build", None)
        try:
            import build  # type: ignore
        except Exception:
            _run([sys.executable, "-m", "pip", "install", "build"])
        else:
            module_path = Path(getattr(build, "__file__", "") or "").resolve()
            try:
                module_path.relative_to(Path.cwd().resolve())
            except ValueError:
                pass
            else:
                # A local ``build`` package shadows the build backend; reinstall it.
                sys.modules.pop("build", None)
                _run([sys.executable, "-m", "pip", "install", "build"])
        _build_in_sanitized_tree(Path.cwd(), generate_wheels=package.generate_wheels)

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
            releases = fetch_pypi_releases(package)
            if version in releases:
                raise ReleaseError(f"Version {version} already on PyPI")
        creds = (
            creds
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
        upload_with_retries(cmd, repository="PyPI")

    if stashed:
        _run(["git", "stash", "pop"], check=False)


def promote(
    *,
    package: Package = DEFAULT_PACKAGE,
    version: str,
    creds: Optional[Credentials] = None,
    stash: bool = False,
) -> None:
    """Build the package and commit the release on the current branch."""
    stashed = False
    if not _git_clean():
        if stash:
            _run(["git", "stash", "--include-untracked"])
            stashed = True
        else:
            raise ReleaseError("Git repository is not clean")

    try:
        build(
            package=package,
            version=version,
            creds=creds,
            tests=False,
            dist=True,
            git=False,
            tag=False,
            stash=stash,
        )
        _run(["git", "add", "."])  # add all changes
        if _git_has_staged_changes():
            _run(["git", "commit", "-m", f"Release v{version}"])
    finally:
        if stashed:
            _run(["git", "stash", "pop"], check=False)
