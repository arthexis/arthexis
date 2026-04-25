from pathlib import Path

from scripts import check_migration_conflicts as conflicts


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _migration(dependencies: list[tuple[str, str]]) -> str:
    dependency_lines = "\n".join(f"        ({app!r}, {migration!r})," for app, migration in dependencies)
    return f"""from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
{dependency_lines}
    ]
    operations = []
"""


def test_alias_app_label_dependencies_do_not_create_false_duplicate_leaves(tmp_path):
    app_dir = tmp_path / "apps" / "sites"
    _write(
        app_dir / "apps.py",
        'from django.apps import AppConfig\n\n\nclass PagesConfig(AppConfig):\n    name = "apps.sites"\n    label = "pages"\n',
    )
    _write(app_dir / "migrations" / "0001_initial.py", _migration([]))
    _write(
        app_dir / "migrations" / "0002_initial.py",
        _migration([("pages", "0001_initial")]),
    )
    _write(
        app_dir / "migrations" / "0003_initial.py",
        _migration([("pages", "0002_initial")]),
    )

    files = conflicts._migration_files_for_app(app_dir)

    assert {migration.migration_label for migration in files} == {"pages"}
    assert conflicts._check_app(files, repo_root=tmp_path) == []


def test_duplicate_leaf_detection_still_uses_default_directory_label(tmp_path):
    app_dir = tmp_path / "apps" / "widgets"
    _write(app_dir / "migrations" / "0001_initial.py", _migration([]))
    _write(
        app_dir / "migrations" / "0002_add_name_pr7424.py",
        _migration([("widgets", "0001_initial")]),
    )
    _write(
        app_dir / "migrations" / "0003_add_slug_pr7424.py",
        _migration([("widgets", "0001_initial")]),
    )

    errors = conflicts._check_app(conflicts._migration_files_for_app(app_dir), repo_root=tmp_path)

    assert len(errors) == 1
    assert "duplicate leaf migrations detected" in errors[0]
    assert "app 'widgets'" in errors[0]
