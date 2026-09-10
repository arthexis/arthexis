from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def test_package_data_is_limited_to_django_assets() -> None:
    with PYPROJECT.open("rb") as stream:
        config = tomllib.load(stream)

    package_data = config["tool"]["setuptools"]["package-data"]

    assert package_data == {
        "*": [
            "templates/**/*",
            "static/**/*",
            "locale/**/*",
        ]
    }


def test_package_data_does_not_reintroduce_unrestricted_recursion() -> None:
    with PYPROJECT.open("rb") as stream:
        config = tomllib.load(stream)

    package_data = config["tool"]["setuptools"]["package-data"]

    for patterns in package_data.values():
        assert "**/*" not in patterns
