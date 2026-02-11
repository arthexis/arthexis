"""GitHub integration helpers for release publishing."""

from __future__ import annotations

import time
from typing import Callable, Protocol
from urllib.parse import urlparse


class GitHubRequestAdapter(Protocol):
    """Transport adapter used for GitHub HTTP requests."""

    def __call__(self, method: str, url: str, **kwargs): ...


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


def fetch_publish_workflow_run(
    *,
    request: GitHubRequestAdapter,
    owner: str,
    repo: str,
    tag_name: str,
    tag_sha: str | None,
    token: str | None,
) -> dict[str, object] | None:
    """Fetch the latest publish workflow run for a release tag.

    The primary query targets the tag branch, then falls back to an unfiltered
    query and matches by ``head_sha`` when branch metadata is unavailable.
    """

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
    fallback_runs = (
        fallback_payload.get("workflow_runs") if isinstance(fallback_payload, dict) else None
    )
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
    """Poll workflow status until completion or timeout.

    Prerequisites:
    * ``fetch_run`` returns workflow payload with optional ``status`` field.

    Side effects:
    * Repeatedly invokes the ``fetch_run`` callable and sleeps between calls.

    Rollback expectations:
    * None; polling is read-only and can be retried by callers.
    """

    deadline = monotonic() + timeout_seconds
    while monotonic() <= deadline:
        run = fetch_run()
        if not run:
            sleep(interval_seconds)
            continue
        status = run.get("status")
        if status == "completed":
            return run
        sleep(interval_seconds)
    return None
