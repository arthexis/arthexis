"""GitHub integration helpers for release publishing.

Responsibilities:
- Parse repository identity and resolve publish credentials.
- Interact with GitHub release/workflow APIs through injected request adapters.

Allowed dependencies:
- May use typed adapters and stdlib URL/time helpers.
- Must not import Django HTTP view modules or execute git subprocess commands.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlparse

from apps.repos.models import GitHubToken


class GitHubRequestAdapter(Protocol):
    """Transport adapter used for GitHub HTTP requests."""

    def __call__(self, method: str, url: str, **kwargs): ...


def get_user_github_token(user) -> GitHubToken | None:
    """Return the first persisted GitHub token for the authenticated user."""

    if not user or not getattr(user, "is_authenticated", False):
        return None
    return GitHubToken.objects.filter(user=user).first()


def resolve_github_token(release, ctx: dict, *, user=None) -> str | None:
    """Resolve a GitHub token from in-memory context, account storage, or release."""

    token = (ctx.get("github_token") or "").strip()
    if token:
        return token
    stored = get_user_github_token(user)
    if stored:
        return (stored.token or "").strip() or None
    return release.get_github_token()


def parse_github_repository(repo_url: str) -> tuple[str, str] | None:
    """Parse owner/repo from SSH or HTTPS GitHub URLs."""

    repo_url = (repo_url or "").strip()
    if not repo_url:
        return None
    if repo_url.startswith("git@"):
        if "github.com" not in repo_url:
            return None
        _, _, path = repo_url.partition("github.com:")
        path = path.strip("/")
    else:
        parsed = urlparse(repo_url)
        if "github.com" not in parsed.netloc.lower():
            return None
        path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def ensure_github_release(
    *,
    request: GitHubRequestAdapter,
    owner: str,
    repo: str,
    tag_name: str,
    token: str | None,
) -> dict[str, object]:
    """Ensure a GitHub release exists for ``tag_name`` and return payload."""

    release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag_name}"
    response = request(
        "get",
        release_url,
        token=token,
        expected_status={200, 404},
    )
    if response.status_code == 404:
        create_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        response = request(
            "post",
            create_url,
            token=token,
            expected_status={201},
            json={"tag_name": tag_name, "name": tag_name},
        )
    elif response.status_code != 200:
        detail = response.text.strip()
        raise Exception(f"GitHub release lookup failed ({response.status_code}): {detail}")
    data = response.json()
    if not isinstance(data, dict):
        raise Exception("GitHub release response was not a JSON object")
    return data


def upload_release_assets(
    *,
    request: GitHubRequestAdapter,
    owner: str,
    repo: str,
    release_data: dict[str, object],
    token: str | None,
    artifacts: Sequence[Path],
    append_log: Callable[[Path, str], None],
    log_path: Path,
) -> None:
    """Upload build artifacts to the target GitHub release."""

    assets = release_data.get("assets") or []
    existing_assets: dict[str, int] = {}
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = asset.get("name")
            asset_id = asset.get("id")
            if isinstance(name, str) and isinstance(asset_id, int):
                existing_assets[name] = asset_id

    release_id = release_data.get("id")
    if not isinstance(release_id, int):
        raise Exception("GitHub release ID missing")

    for artifact in artifacts:
        name = artifact.name
        existing_id = existing_assets.get(name)
        if existing_id:
            delete_url = f"https://api.github.com/repos/{owner}/{repo}/releases/assets/{existing_id}"
            request("delete", delete_url, token=token, expected_status={204})
            append_log(log_path, f"Removed existing GitHub asset {name}")

        upload_url = (
            f"https://uploads.github.com/repos/{owner}/{repo}/releases/{release_id}/assets?name={name}"
        )
        with artifact.open("rb") as handle:
            request(
                "post",
                upload_url,
                token=token,
                expected_status={201},
                headers={"Content-Type": "application/octet-stream"},
                data=handle,
            )
        append_log(log_path, f"Uploaded GitHub release asset {name}")


def fetch_publish_workflow_run(
    *,
    request: GitHubRequestAdapter,
    owner: str,
    repo: str,
    tag_name: str,
    tag_sha: str | None,
    token: str | None,
) -> dict[str, object] | None:
    """Fetch the latest publish workflow run for a release tag."""

    runs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/publish.yml/runs"

    response = request(
        "get",
        runs_url,
        token=token,
        expected_status={200},
        params={"event": "push", "branch": tag_name, "per_page": 5},
    )
    payload = response.json()
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return None

    for run in runs:
        if not isinstance(run, dict):
            continue
        run_head_sha = run.get("head_sha") if isinstance(run.get("head_sha"), str) else None
        if run.get("head_branch") == tag_name or (tag_sha and run_head_sha == tag_sha):
            return run

    if not tag_sha:
        return None

    fallback = request(
        "get",
        runs_url,
        token=token,
        expected_status={200},
        params={"event": "push", "per_page": 20},
    )
    fallback_payload = fallback.json()
    fallback_runs = fallback_payload.get("workflow_runs") if isinstance(fallback_payload, dict) else None
    if not isinstance(fallback_runs, list):
        return None
    for run in fallback_runs:
        if isinstance(run, dict) and run.get("head_sha") == tag_sha:
            return run
    return None


def poll_workflow_completion(
    *,
    fetch_run: Callable[[], dict[str, object] | None],
    timeout_seconds: float,
    interval_seconds: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object] | None:
    """Poll workflow status until completion or timeout."""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    deadline = monotonic() + timeout_seconds
    while monotonic() <= deadline:
        run = fetch_run()
        if not run:
            sleep(interval_seconds)
            continue
        if run.get("status") == "completed":
            return run
        sleep(interval_seconds)
    return None
