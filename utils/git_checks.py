"""Utilities for validating repository metadata."""

from __future__ import annotations

from pathlib import Path


def _is_within(path: Path, ancestor: Path) -> bool:
    """Return ``True`` if *path* is located inside *ancestor*."""

    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True


def find_nested_git_repositories(base_path: Path) -> list[Path]:
    """Return relative paths of nested Git repositories.

    The search looks for ``.git`` entries that live outside of the root
    repository metadata directory. Both nested repositories created via
    ``git init`` (which create a directory) and gitlinks used for submodules
    (which create a file) are detected.
    """

    base_path = base_path.resolve()
    root_git = base_path / ".git"
    root_git_dir: Path | None = root_git if root_git.is_dir() else None

    nested: set[Path] = set()

    for marker in base_path.rglob(".git"):
        if marker == root_git:
            continue
        if root_git_dir and _is_within(marker, root_git_dir):
            continue

        parent = marker.parent

        try:
            relative = parent.relative_to(base_path)
        except ValueError:
            continue

        nested.add(relative)

    return sorted(nested)


__all__ = ["find_nested_git_repositories"]
