from __future__ import annotations

from importlib import import_module
from typing import Any

from .services import (
    Credentials,
    DEFAULT_PACKAGE,
    DEFAULT_PACKAGE_MODULES,
    GitCredentials,
    Package,
    PostPublishWarning,
    PyPICheckResult,
    ReleaseError,
    RepositoryTarget,
    TestsFailed,
    _build_in_sanitized_tree,
    _export_tracked_files,
    _git_clean,
    _git_has_staged_changes,
    _has_porcelain_changes,
    _ignored_working_tree_paths,
    _is_git_repository,
    _run,
    _temporary_working_directory,
    _write_pyproject,
    build,
    check_pypi_readiness,
    fetch_pypi_releases,
    is_retryable_twine_error,
    network_available,
    promote,
    publish,
    requires_network,
    run_tests,
    upload_with_retries,
)

__all__ = [
    "Credentials",
    "DEFAULT_PACKAGE",
    "DEFAULT_PACKAGE_MODULES",
    "GitCredentials",
    "Package",
    "PostPublishWarning",
    "PyPICheckResult",
    "ReleaseError",
    "RepositoryTarget",
    "TestsFailed",
    "_build_in_sanitized_tree",
    "_export_tracked_files",
    "_git_clean",
    "_git_has_staged_changes",
    "_has_porcelain_changes",
    "_ignored_working_tree_paths",
    "_is_git_repository",
    "_run",
    "_temporary_working_directory",
    "_write_pyproject",
    "build",
    "check_pypi_readiness",
    "fetch_pypi_releases",
    "is_retryable_twine_error",
    "network_available",
    "promote",
    "publish",
    "requires_network",
    "run_tests",
    "upload_with_retries",
]



def __getattr__(name: str) -> Any:
    if name == "release":
        module = import_module(".release", __name__)
        globals()["release"] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + ["release"])
