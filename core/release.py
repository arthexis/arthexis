from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote, urlsplit, urlunsplit

try:  # pragma: no cover - optional dependency
    import toml  # type: ignore
except Exception:  # pragma: no cover - fallback when missing
    toml = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import requests  # type: ignore
except Exception:  # pragma: no cover - fallback when missing
    requests = None  # type: ignore

from config.offline import requires_network, network_available


DEFAULT_PACKAGE_MODULES = [
    "core",
    "config",
    "nodes",
    "ocpp",
    "pages",
]


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
    packages: Sequence[str] = tuple(DEFAULT_PACKAGE_MODULES)
    version_path: Optional[Path | str] = None
    dependencies_path: Optional[Path | str] = None
    test_command: Optional[str] = None


@dataclass
class Credentials:
    """Credentials for uploading to PyPI."""

    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    def has_auth(self) -> bool:
        return bool(self.token) or bool(self.username and self.password)

    def twine_args(self) -> list[str]:
        if self.token:
            return ["--username", "__token__", "--password", self.token]
        if self.username and self.password:
            return ["--username", self.username, "--password", self.password]
        raise ValueError("Missing PyPI credentials")


@dataclass
class GitCredentials:
    """Credentials used for Git operations such as pushing tags."""

    username: Optional[str] = None
    password: Optional[str] = None

    def has_auth(self) -> bool:
        return bool((self.username or "").strip() and (self.password or "").strip())


@dataclass
class RepositoryTarget:
    """Configuration for uploading a distribution to a repository."""

    name: str
    repository_url: Optional[str] = None
    credentials: Optional[Credentials] = None
    verify_availability: bool = False
    extra_args: Sequence[str] = ()

    def build_command(self, files: Sequence[str]) -> list[str]:
        cmd = [sys.executable, "-m", "twine", "upload", *self.extra_args]
        if self.repository_url:
            cmd += ["--repository-url", self.repository_url]
        cmd += list(files)
        return cmd


DEFAULT_PACKAGE = Package(
    name="arthexis",
    description="Power & Energy Infrastructure",
    author="Rafael J. Guillén-Osorio",
    email="tecnologia@gelectriic.com",
    python_requires=">=3.10",
    license="GPL-3.0-only",
)


class ReleaseError(Exception):
    pass


class PostPublishWarning(ReleaseError):
    """Raised when distribution uploads succeed but post-publish tasks need attention."""

    def __init__(
        self,
        message: str,
        *,
        uploaded: Sequence[str],
        followups: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(message)
        self.uploaded = list(uploaded)
        self.followups = list(followups or [])


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


def _build_in_sanitized_tree(base_dir: Path) -> None:
    """Run ``python -m build`` from a staging tree containing tracked files."""

    with tempfile.TemporaryDirectory(prefix="arthexis-build-") as temp_dir:
        staging_root = Path(temp_dir)
        _export_tracked_files(base_dir, staging_root)
        _run([sys.executable, "-m", "build"], cwd=staging_root)
        built_dist = staging_root / "dist"
        if not built_dist.exists():
            raise ReleaseError("dist directory not created")
        destination_dist = base_dir / "dist"
        if destination_dist.exists():
            shutil.rmtree(destination_dist)
        shutil.copytree(built_dist, destination_dist)


_RETRYABLE_TWINE_ERRORS = (
    "connectionreseterror",
    "connection aborted",
    "protocolerror",
    "forcibly closed by the remote host",
    "remote host closed the connection",
)


def _is_retryable_twine_error(output: str) -> bool:
    normalized = output.lower()
    return any(marker in normalized for marker in _RETRYABLE_TWINE_ERRORS)


def _upload_with_retries(
    cmd: list[str],
    *,
    repository: str,
    retries: int = 3,
    cooldown: float = 3.0,
) -> None:
    last_output = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        if proc.returncode == 0:
            return

        combined = (stdout + stderr).strip()
        last_output = combined or f"Twine exited with code {proc.returncode}"

        if attempt < retries and _is_retryable_twine_error(combined):
            time.sleep(cooldown)
            continue

        if _is_retryable_twine_error(combined):
            raise ReleaseError(
                "Twine upload to {repo} failed after {attempts} attempts due to a network interruption. "
                "Check your internet connection, wait a moment, then rerun the release command. "
                "If uploads continue to fail, manually run `python -m twine upload dist/*` once the network "
                "stabilizes or contact the release manager for assistance.\n\nLast error:\n{error}".format(
                    repo=repository, attempts=attempt, error=last_output
                )
            )

        raise ReleaseError(last_output)

    raise ReleaseError(last_output)


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


def _manager_git_credentials(package: Optional[Package] = None) -> Optional[GitCredentials]:
    """Return Git credentials from the Package's release manager if available."""

    try:  # pragma: no cover - optional dependency
        from core.models import Package as PackageModel

        queryset = PackageModel.objects.select_related("release_manager")
        if package is not None:
            queryset = queryset.filter(name=package.name)
        package_obj = queryset.first()
        if package_obj and package_obj.release_manager:
            creds = package_obj.release_manager.to_git_credentials()
            if creds and creds.has_auth():
                return creds
    except Exception:
        return None
    return None


def _git_authentication_missing(exc: subprocess.CalledProcessError) -> bool:
    message = (exc.stderr or exc.stdout or "").strip().lower()
    if not message:
        return False
    auth_markers = [
        "could not read username",
        "authentication failed",
        "fatal: authentication failed",
        "terminal prompts disabled",
    ]
    return any(marker in message for marker in auth_markers)


def _format_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    return (exc.stderr or exc.stdout or str(exc)).strip() or str(exc)


def _git_remote_url(remote: str = "origin") -> Optional[str]:
    proc = subprocess.run(
        ["git", "remote", "get-url", remote],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _git_tag_commit(tag_name: str) -> Optional[str]:
    """Return the commit referenced by ``tag_name`` in the local repository."""

    for ref in (f"{tag_name}^{{}}", tag_name):
        proc = subprocess.run(
            ["git", "rev-parse", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            commit = (proc.stdout or "").strip()
            if commit:
                return commit
    return None


def _git_remote_tag_commit(remote: str, tag_name: str) -> Optional[str]:
    """Return the commit referenced by ``tag_name`` on ``remote`` if it exists."""

    proc = subprocess.run(
        ["git", "ls-remote", "--tags", remote, tag_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None

    commit = None
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        commit = sha
        if ref.endswith("^{}"):
            return sha
    return commit


def _remote_with_credentials(url: str, creds: GitCredentials) -> Optional[str]:
    if not creds.has_auth():
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.split("@", 1)[-1]
    username = quote((creds.username or "").strip(), safe="")
    password = quote((creds.password or "").strip(), safe="")
    netloc = f"{username}:{password}@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _raise_git_authentication_error(tag_name: str, exc: subprocess.CalledProcessError) -> None:
    details = _format_subprocess_error(exc)
    message = (
        "Git authentication failed while pushing tag {tag}. "
        "Configure Git credentials in the release manager profile or authenticate "
        "locally, then rerun the publish step or push the tag manually with `git push "
        "origin {tag}`."
    ).format(tag=tag_name)
    if details:
        message = f"{message} Git reported: {details}"
    raise ReleaseError(message) from exc


def _push_tag(tag_name: str, package: Package) -> None:
    auth_error: subprocess.CalledProcessError | None = None
    try:
        _run(["git", "push", "origin", tag_name])
        return
    except subprocess.CalledProcessError as exc:
        remote_commit = _git_remote_tag_commit("origin", tag_name)
        local_commit = _git_tag_commit(tag_name)
        if remote_commit:
            if local_commit and remote_commit == local_commit:
                # Another process already pushed the tag; treat as success.
                return
            message = (
                "Git rejected tag {tag} because it already exists on the remote. "
                "Delete the remote tag or choose a new version before retrying."
            ).format(tag=tag_name)
            raise ReleaseError(message) from exc
        if not _git_authentication_missing(exc):
            raise
        auth_error = exc

    creds = _manager_git_credentials(package)
    if creds and creds.has_auth():
        remote_url = _git_remote_url("origin")
        if remote_url:
            authed_url = _remote_with_credentials(remote_url, creds)
            if authed_url:
                try:
                    _run(["git", "push", authed_url, tag_name])
                    return
                except subprocess.CalledProcessError as push_exc:
                    if not _git_authentication_missing(push_exc):
                        raise
                    auth_error = push_exc
    # If we reach this point, the original exception is an auth error
    if auth_error is not None:
        _raise_git_authentication_error(tag_name, auth_error)
    raise ReleaseError(
        "Git authentication failed while pushing tag {tag}. Configure Git credentials and try again.".format(
            tag=tag_name
        )
    )


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
            "setuptools": {"packages": list(package.packages)}
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
        _build_in_sanitized_tree(Path.cwd())

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
        _upload_with_retries(cmd, repository="PyPI")

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
    repositories: Optional[Sequence[RepositoryTarget]] = None,
) -> list[str]:
    """Upload the existing distribution to one or more repositories."""

    def _resolve_primary_credentials(target: RepositoryTarget) -> Credentials:
        if target.credentials is not None:
            try:
                target.credentials.twine_args()
            except ValueError as exc:
                raise ReleaseError(f"Missing credentials for {target.name}") from exc
            return target.credentials

        candidate = (
            creds
            or _manager_credentials()
            or Credentials(
                token=os.environ.get("PYPI_API_TOKEN"),
                username=os.environ.get("PYPI_USERNAME"),
                password=os.environ.get("PYPI_PASSWORD"),
            )
        )
        if candidate is None or not candidate.has_auth():
            raise ReleaseError("Missing PyPI credentials")
        try:
            candidate.twine_args()
        except ValueError as exc:  # pragma: no cover - validated above
            raise ReleaseError("Missing PyPI credentials") from exc
        target.credentials = candidate
        return candidate

    repository_targets: list[RepositoryTarget]
    if repositories is None:
        primary = RepositoryTarget(name="PyPI", verify_availability=True)
        repository_targets = [primary]
    else:
        repository_targets = list(repositories)
        if not repository_targets:
            raise ReleaseError("No repositories configured")

    primary = repository_targets[0]

    if network_available() and primary.verify_availability and requests is not None:
        resp = requests.get(f"https://pypi.org/pypi/{package.name}/json")
        if resp.ok and version in resp.json().get("releases", {}):
            raise ReleaseError(f"Version {version} already on PyPI")

    if not Path("dist").exists():
        raise ReleaseError("dist directory not found")
    files = sorted(str(p) for p in Path("dist").glob("*"))
    if not files:
        raise ReleaseError("dist directory is empty")

    primary_credentials = _resolve_primary_credentials(primary)

    uploaded: list[str] = []
    for index, target in enumerate(repository_targets):
        creds_obj = target.credentials
        if creds_obj is None:
            if index == 0:
                creds_obj = primary_credentials
            else:
                raise ReleaseError(f"Missing credentials for {target.name}")
        try:
            auth_args = creds_obj.twine_args()
        except ValueError as exc:
            label = "PyPI" if index == 0 else target.name
            raise ReleaseError(f"Missing credentials for {label}") from exc
        cmd = target.build_command(files) + auth_args
        _upload_with_retries(cmd, repository=target.name)
        uploaded.append(target.name)

    tag_name = f"v{version}"
    try:
        _run(["git", "tag", tag_name])
    except subprocess.CalledProcessError as exc:
        details = _format_subprocess_error(exc)
        if uploaded:
            uploads = ", ".join(uploaded)
            if details:
                message = (
                    f"Upload to {uploads} completed, but creating git tag {tag_name} failed: {details}"
                )
            else:
                message = (
                    f"Upload to {uploads} completed, but creating git tag {tag_name} failed."
                )
            followups = [f"Create and push git tag {tag_name} manually once the repository is ready."]
            raise PostPublishWarning(
                message,
                uploaded=uploaded,
                followups=followups,
            ) from exc
        raise ReleaseError(
            f"Failed to create git tag {tag_name}: {details or exc}"
        ) from exc

    try:
        _push_tag(tag_name, package)
    except ReleaseError as exc:
        if uploaded:
            uploads = ", ".join(uploaded)
            message = f"Upload to {uploads} completed, but {exc}"
            followups = [
                f"Push git tag {tag_name} to origin after resolving the reported issue."
            ]
            warning = PostPublishWarning(
                message,
                uploaded=uploaded,
                followups=followups,
            )
            raise warning from exc
        raise
    return uploaded


@dataclass
class PyPICheckResult:
    ok: bool
    messages: list[tuple[str, str]]


def check_pypi_readiness(
    *,
    release: Optional["PackageRelease"] = None,
    package: Optional[Package] = None,
    creds: Optional[Credentials] = None,
    repositories: Optional[Sequence[RepositoryTarget]] = None,
) -> PyPICheckResult:
    """Validate connectivity and credentials required for PyPI uploads."""

    messages: list[tuple[str, str]] = []
    has_error = False

    def add(level: str, message: str) -> None:
        nonlocal has_error
        messages.append((level, message))
        if level == "error":
            has_error = True

    release_manager = None
    if release is not None:
        package = release.to_package()
        repositories = release.build_publish_targets()
        creds = release.to_credentials()
        release_manager = release.release_manager or release.package.release_manager
        add("success", f"Checking PyPI configuration for {release}")

    if package is None:
        package = DEFAULT_PACKAGE

    if repositories is None:
        repositories = [RepositoryTarget(name="PyPI", verify_availability=True)]
    else:
        repositories = list(repositories)

    if not repositories:
        add("error", "No repositories configured for upload")
        return PyPICheckResult(ok=False, messages=messages)

    if release_manager is not None:
        if release_manager.pypi_token or (
            release_manager.pypi_username and release_manager.pypi_password
        ):
            add(
                "success",
                f"Release manager '{release_manager}' has PyPI credentials configured",
            )
        else:
            add(
                "warning",
                f"Release manager '{release_manager}' is missing PyPI credentials",
            )
    else:
        add(
            "warning",
            "No release manager configured for PyPI uploads; falling back to environment",
        )

    env_creds = Credentials(
        token=os.environ.get("PYPI_API_TOKEN"),
        username=os.environ.get("PYPI_USERNAME"),
        password=os.environ.get("PYPI_PASSWORD"),
    )
    if not env_creds.has_auth():
        env_creds = None

    primary = repositories[0]
    candidate = primary.credentials
    credential_source = "repository"
    if candidate is None and creds is not None and creds.has_auth():
        candidate = creds
        credential_source = "release manager"
    if candidate is None and env_creds is not None:
        candidate = env_creds
        credential_source = "environment"

    if candidate is None:
        add(
            "error",
            "Missing PyPI credentials. Configure a token or username/password for the release manager or environment.",
        )
    else:
        try:
            candidate.twine_args()
        except ValueError as exc:
            add("error", f"Invalid PyPI credentials: {exc}")
        else:
            auth_kind = "API token" if candidate.token else "username/password"
            if credential_source == "release manager":
                add("success", f"Using {auth_kind} provided by the release manager")
            elif credential_source == "environment":
                add("success", f"Using {auth_kind} from environment variables")
            elif credential_source == "repository":
                add("success", f"Using {auth_kind} supplied by repository target configuration")

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "twine", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        add("error", "Twine is not installed. Install it with `pip install twine`.")
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        add(
            "error",
            f"Twine version check failed: {output.strip() or exc.returncode}",
        )
    else:
        version_info = (proc.stdout or proc.stderr or "").strip()
        if version_info:
            add("success", f"Twine available: {version_info}")
        else:
            add("success", "Twine version check succeeded")

    if not network_available():
        add(
            "warning",
            "Offline mode enabled; skipping network connectivity checks",
        )
        return PyPICheckResult(ok=not has_error, messages=messages)

    if requests is None:
        add("warning", "requests library unavailable; skipping network checks")
        return PyPICheckResult(ok=not has_error, messages=messages)

    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{package.name}/json", timeout=10
        )
    except Exception as exc:  # pragma: no cover - network failure
        add("error", f"Failed to reach PyPI JSON API: {exc}")
    else:
        if resp.ok:
            add(
                "success",
                f"PyPI JSON API reachable for project '{package.name}'",
            )
        else:
            add(
                "error",
                f"PyPI JSON API returned status {resp.status_code} for '{package.name}'",
            )

    checked_urls: set[str] = set()
    for target in repositories:
        url = target.repository_url or "https://upload.pypi.org/legacy/"
        if url in checked_urls:
            continue
        checked_urls.add(url)
        try:
            resp = requests.get(url, timeout=10)
        except Exception as exc:  # pragma: no cover - network failure
            add("error", f"Failed to reach upload endpoint {url}: {exc}")
            continue
        if resp.ok:
            add(
                "success",
                f"Upload endpoint {url} responded with status {resp.status_code}",
            )
        else:
            add(
                "error",
                f"Upload endpoint {url} returned status {resp.status_code}",
            )

    return PyPICheckResult(ok=not has_error, messages=messages)
