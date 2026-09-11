import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
EXPECTED_PACKAGE_DATA = {
    "templates/**/*",
    "static/**/*",
    "locale/**/*",
}


def _package_data() -> dict[str, list[str]]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["setuptools"]["package-data"]


def test_package_data_is_limited_to_runtime_assets() -> None:
    package_data = _package_data()

    assert set(package_data) == {"*"}
    assert set(package_data["*"]) == EXPECTED_PACKAGE_DATA


def test_package_data_has_no_unrestricted_recursive_glob() -> None:
    package_data = _package_data()

    patterns = {
        pattern.strip()
        for package_patterns in package_data.values()
        for pattern in package_patterns
    }

    assert "**/*" not in patterns
