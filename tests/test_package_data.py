from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def _load_package_data() -> dict[str, list[str]]:
    pyproject = ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]


def _covered_by_package_data(package_root: Path, file_path: Path, patterns: list[str]) -> bool:
    return any(file_path in package_root.glob(pattern) for pattern in patterns)


def test_souls_registration_templates_are_in_package_data():
    package_name = "apps.souls"
    package_root = ROOT / Path(*package_name.split("."))
    package_data = _load_package_data()
    patterns = package_data.get(package_name, [])

    assert "**/*" in patterns

    templates = sorted((package_root / "templates" / "souls").glob("*.html"))
    assert templates
    assert all(_covered_by_package_data(package_root, template, patterns) for template in templates)
