"""Tests for release package metadata consistency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

from apps.release.services.builder import _write_pyproject
from apps.release.services.defaults import DEFAULT_PACKAGE

pytestmark = pytest.mark.pr_origin(6306)


def _load_pyproject_license(path: Path) -> str:
    """Return the published project license from a ``pyproject.toml`` file."""

    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]["license"]


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

    root = Path(__file__).resolve().parents[3]
    expected_license = (root / "LICENSE").read_text(encoding="utf-8").splitlines()[0]

    assert DEFAULT_PACKAGE.license == expected_license
    assert loader(root / relative_path) == expected_license


def test_write_pyproject_uses_package_license(tmp_path, monkeypatch) -> None:
    """Generated package metadata should preserve the configured package license string."""

    package = DEFAULT_PACKAGE
    monkeypatch.chdir(tmp_path)

    _write_pyproject(package, "9.9.9", ["Django==5.2.12"])

    pyproject = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["license"] == package.license
