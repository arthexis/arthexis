"""Ensure fixture directories are declared in package data."""

from pathlib import Path

import toml


def test_fixture_directories_are_in_package_data() -> None:
    """All fixture directories should correspond to package-data entries."""

    pyproject = toml.load(Path("pyproject.toml"))
    package_data_keys = set(
        pyproject["tool"]["setuptools"]["package-data"].keys()
    )

    fixture_parents = {
        ".".join(path.relative_to(Path(".")).parts[:-1])
        for path in Path("apps").rglob("fixtures")
        if path.is_dir()
    }

    missing = fixture_parents - package_data_keys

    assert not missing, (
        "Fixture directories found outside package-data entries: "
        f"{sorted(missing)}"
    )
