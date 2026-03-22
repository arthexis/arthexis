"""Top-level package for the Arthexis distribution.

This module provides a stable import target for environments that install the
repository as the ``arthexis`` Python distribution, including editable installs.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _find_repo_root(start: Path) -> Path | None:
    """Locate the repository root by walking upward to ``pyproject.toml``.

    Args:
        start: Directory from which to begin the upward search.

    Returns:
        The repository root when ``pyproject.toml`` is found, otherwise ``None``.
    """

    current = start
    while True:
        if (current / "pyproject.toml").is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _read_repo_version_file() -> str | None:
    """Read the repository ``VERSION`` file when running from a source checkout.

    Returns:
        The trimmed version string from ``VERSION``, or ``None`` when the file
        cannot be resolved from the current module location.
    """

    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    if repo_root is None:
        return None

    version_path = repo_root / "VERSION"
    if not version_path.exists():
        return None

    return version_path.read_text(encoding="utf-8").strip() or None


def _resolve_version() -> str:
    """Return the runtime Arthexis version for installed and source checkouts.

    Returns:
        The repository ``VERSION`` value when available, otherwise the installed
        distribution version, or ``"0+unknown"`` when neither source is
        available.
    """

    repo_version = _read_repo_version_file()
    if repo_version is not None:
        return repo_version

    try:
        return version("arthexis")
    except PackageNotFoundError:
        return "0+unknown"


__all__ = ["__version__"]
__version__ = _resolve_version()
