from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from apps.release import git_utils

from .builder import _run
from .defaults import DEFAULT_PACKAGE
from .models import Credentials, GitCredentials, Package, ReleaseError, RepositoryTarget
from .network import close_response, fetch_pypi_releases, is_retryable_twine_error, network_available, requests


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


@dataclass
class PyPICheckResult:
    ok: bool
    messages: list[tuple[str, str]]


def upload_with_retries(
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

        if attempt < retries and is_retryable_twine_error(combined):
            time.sleep(cooldown)
            continue

        if is_retryable_twine_error(combined):
            raise ReleaseError(
                "Twine upload to {repo} failed after {attempts} attempts due to a network interruption. "
                "Check your internet connection, wait a moment, then rerun the release command. "
                "If uploads continue to fail, manually run `python -m twine upload dist/*` once the network "
                "stabilizes.\n\nLast error:\n{error}".format(
                    repo=repository, attempts=attempt, error=last_output
                )
            )

        raise ReleaseError(last_output)

    raise ReleaseError(last_output)


def _environment_git_credentials() -> Optional[GitCredentials]:
    """Return Git credentials from environment variables when available."""

    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        return None
    return GitCredentials(username="x-access-token", password=token)


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


def _raise_git_authentication_error(tag_name: str, exc: subprocess.CalledProcessError) -> None:
    details = _format_subprocess_error(exc)
    message = (
        "Git authentication failed while pushing tag {tag}. "
        "Configure your local environment to authenticate with the repository "
        "(for example, set up an SSH key or configure a GitHub token in your git "
        "credential helper), then rerun the publish step or push the tag manually "
        "with `git push origin {tag}`. "
        "See docs/development/package-release-process.md#git-authentication-for-tag-pushes."
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

    creds = _environment_git_credentials()
    if creds and creds.has_auth():
        remote_url = git_utils.git_remote_url("origin")
        if remote_url:
            authed_url = git_utils.remote_url_with_credentials(
                remote_url,
                username=(creds.username or "").strip(),
                password=(creds.password or "").strip(),
            )
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
        repository_targets = list(getattr(package, "repositories", ()) or ())
        if not repository_targets:
            primary = RepositoryTarget(name="PyPI", verify_availability=True)
            repository_targets = [primary]
    else:
        repository_targets = list(repositories)
        if not repository_targets:
            raise ReleaseError("No repositories configured")

    primary = repository_targets[0]

    if primary.verify_availability:
        releases = fetch_pypi_releases(package)
        if version in releases:
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
        upload_with_retries(cmd, repository=target.name)
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
    oidc_enabled = False

    def add(level: str, message: str) -> None:
        nonlocal has_error
        messages.append((level, message))
        if level == "error":
            has_error = True

    if release is not None:
        package = release.to_package()
        repositories = release.build_publish_targets()
        creds = release.to_credentials()
        oidc_enabled = release.uses_oidc_publishing()
        add("success", f"Checking PyPI configuration for {release}")

    if package is None:
        package = DEFAULT_PACKAGE

    if repositories is None:
        repositories = list(getattr(package, "repositories", ()) or ())
        if not repositories:
            repositories = [RepositoryTarget(name="PyPI", verify_availability=True)]
    else:
        repositories = list(repositories)

    if not repositories:
        add("error", "No repositories configured for upload")
        return PyPICheckResult(ok=False, messages=messages)

    if oidc_enabled:
        add(
            "success",
            "OIDC publishing enabled; PyPI credentials are not required.",
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
        credential_source = "release context"
    if candidate is None and env_creds is not None:
        candidate = env_creds
        credential_source = "environment"

    if candidate is None:
        if oidc_enabled:
            add(
                "warning",
                "No PyPI credentials configured; OIDC publishing will be used.",
            )
            return PyPICheckResult(ok=not has_error, messages=messages)
        add(
            "error",
            "Missing PyPI credentials. Configure a token or username/password in the environment.",
        )
    else:
        try:
            candidate.twine_args()
        except ValueError as exc:
            add("error", f"Invalid PyPI credentials: {exc}")
        else:
            auth_kind = "API token" if candidate.token else "username/password"
            if credential_source == "release context":
                add("success", f"Using {auth_kind} provided by the release context")
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

    resp = None
    try:
        resp = requests.get(f"https://pypi.org/pypi/{package.name}/json", timeout=10)
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
    finally:
        close_response(resp)

    checked_urls: set[str] = set()
    for target in repositories:
        url = target.repository_url or "https://upload.pypi.org/legacy/"
        if url in checked_urls:
            continue
        checked_urls.add(url)
        resp = None
        try:
            resp = requests.get(url, timeout=10)
        except Exception as exc:  # pragma: no cover - network failure
            add("error", f"Failed to reach upload endpoint {url}: {exc}")
            continue

        try:
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
        finally:
            close_response(resp)

    return PyPICheckResult(ok=not has_error, messages=messages)
