"""Compatibility re-exports for GitHub release publishing helpers.

Legacy import path: ``release_publish.integrations.github``.
"""

from apps.release.publishing.github_ops import (  # noqa: F401
    GitHubRequestAdapter,
    ensure_github_release,
    fetch_publish_workflow_run,
    parse_github_repository,
    poll_workflow_completion,
    resolve_github_token,
    upload_release_assets,
)
