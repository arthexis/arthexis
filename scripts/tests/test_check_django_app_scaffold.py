"""Regression tests for Django app scaffold checks."""

from __future__ import annotations

from pathlib import Path

from scripts import check_django_app_scaffold


def test_collect_missing_scaffold_paths_reports_empty_when_complete(tmp_path: Path, monkeypatch) -> None:
    """Fully scaffolded top-level Django apps should produce no missing-path issues."""

    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "sample"
    (app_dir / "migrations").mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "apps.py").write_text("", encoding="utf-8")
    (app_dir / "migrations" / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(check_django_app_scaffold, "APPS_DIR", apps_dir)
    monkeypatch.setattr(
        check_django_app_scaffold,
        "_is_django_app_dir",
        lambda path: path.name == "sample",
    )
    monkeypatch.setattr(
        check_django_app_scaffold,
        "_to_module_path",
        lambda path: f"apps.{path.name}",
    )
    monkeypatch.setattr(check_django_app_scaffold, "EXCLUDED_AUTO_DISCOVERED_APPS", set())

    assert check_django_app_scaffold.collect_missing_scaffold_paths() == {}


def test_collect_missing_scaffold_paths_reports_missing_migration_init(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Top-level Django apps missing migrations package init should be reported."""

    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "sample"
    app_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "apps.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(check_django_app_scaffold, "APPS_DIR", apps_dir)
    monkeypatch.setattr(
        check_django_app_scaffold,
        "_is_django_app_dir",
        lambda path: path.name == "sample",
    )
    monkeypatch.setattr(
        check_django_app_scaffold,
        "_to_module_path",
        lambda path: f"apps.{path.name}",
    )
    monkeypatch.setattr(check_django_app_scaffold, "EXCLUDED_AUTO_DISCOVERED_APPS", set())

    assert check_django_app_scaffold.collect_missing_scaffold_paths() == {
        "apps.sample": ["migrations/__init__.py"]
    }


def test_collect_missing_scaffold_paths_ignores_explicitly_excluded_packages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Packages listed in exclusions should not be treated as intended Django apps."""

    apps_dir = tmp_path / "apps"
    utility_dir = apps_dir / "camera"
    utility_dir.mkdir(parents=True)
    (utility_dir / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(check_django_app_scaffold, "APPS_DIR", apps_dir)
    monkeypatch.setattr(
        check_django_app_scaffold,
        "_is_django_app_dir",
        lambda path: path.name == "camera",
    )
    monkeypatch.setattr(
        check_django_app_scaffold,
        "_to_module_path",
        lambda path: f"apps.{path.name}",
    )
    monkeypatch.setattr(
        check_django_app_scaffold,
        "EXCLUDED_AUTO_DISCOVERED_APPS",
        {"apps.camera"},
    )

    assert check_django_app_scaffold.collect_missing_scaffold_paths() == {}
