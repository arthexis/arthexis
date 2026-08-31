"""Compatibility re-exports for git operations.

Legacy import path: ``release_publish.services.git_ops``.
"""

from apps.release.publishing.git_ops import (  # noqa: F401
    GitProcessAdapter,
    SubprocessGitAdapter,
    collect_dirty_files,
    current_branch,
    git_stdout,
    has_upstream,
    push_needed,
    working_tree_dirty,
)
