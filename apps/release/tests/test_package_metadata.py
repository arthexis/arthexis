"""Tests for release package metadata consistency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

from apps.release.services.builder import _write_pyproject
from apps.release.services.defaults import DEFAULT_PACKAGE


def _find_repository_root(start: Path) -> Path:
    """Return the repository root by walking upward to the project markers.

    Parameters:
        start: Starting filesystem path.

    Returns:
        The repository root path.

    Raises:
        FileNotFoundError: If no repository marker is found.
    """

    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "LICENSE").exists():
            return candidate
    raise FileNotFoundError("Could not locate repository root from test path")


def _load_pyproject_license(path: Path) -> tuple[str, tuple[str, ...] | None]:
    """Return the published project license and license files from ``pyproject.toml``.

    Parameters:
        path: Path to the TOML file.

    Returns:
        A tuple of the declared license expression and optional license files.
    """

    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    license_files = tuple(project.get("license-files", [])) or None
    return project["license"], license_files


def _load_fixture_license(path: Path) -> str:
    """Return the package license stored in a fixture file."""

    return json.loads(path.read_text(encoding="utf-8"))[0]["fields"]["license"]


@pytest.mark.parametrize(
    ("relative_path", "loader"),
    [
        ("pyproject.toml", _load_pyproject_license),
        ("apps/release/fixtures/packages__arthexis.json", _load_fixture_license),
        ("apps/core/fixtures/packages__arthexis.json", _load_fixture_license),
    ],
)
def test_repository_package_metadata_uses_license_title(relative_path, loader) -> None:
    """Repository metadata should publish the same license title declared in ``LICENSE``."""

    root = _find_repository_root(Path(__file__).resolve())
    expected_license = (root / "LICENSE").read_text(encoding="utf-8").splitlines()[0]

    assert DEFAULT_PACKAGE.license == expected_license
    loaded = loader(root / relative_path)
    if relative_path == "pyproject.toml":
        assert loaded == ("LicenseRef-ArthexisReciprocity", ("LICENSE",))
    else:
        assert loaded == expected_license


def test_write_pyproject_uses_package_license(tmp_path, monkeypatch) -> None:
    """Generated package metadata should preserve the configured package license string."""

    package = DEFAULT_PACKAGE
    monkeypatch.chdir(tmp_path)

    _write_pyproject(package, "9.9.9", ["Django==5.2.12"])

    pyproject = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["license"] == "LicenseRef-ArthexisReciprocity"
    assert pyproject["project"]["license-files"] == ["LICENSE"]
