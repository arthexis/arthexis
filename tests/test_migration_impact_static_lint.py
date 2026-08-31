"""Tests for migration impact reports and static migration lint."""

from __future__ import annotations

from pathlib import Path

from scripts import check_migration_conflicts as checks


def _write_migration(repo_root: Path, relative_path: str, body: str) -> Path:
    path = repo_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_static_lint_detects_current_model_import_and_irreversible_runpython(
    tmp_path,
):
    migration = _write_migration(
        tmp_path,
        "apps/catalog/migrations/0002_seed_widgets_pr8835.py",
        "from django.db import migrations\n"
        "from apps.catalog.models import Widget\n\n"
        "def seed_widgets(apps, schema_editor):\n"
        "    Widget.objects.create(name='demo')\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = []\n"
        "    operations = [migrations.RunPython(seed_widgets)]\n",
    )

    findings = [
        finding.as_payload(repo_root=tmp_path)
        for finding in checks.lint_migration_file(migration, repo_root=tmp_path)
    ]

    assert {finding["code"] for finding in findings} == {
        "current-model-import",
        "runpython-without-reverse",
    }


def test_static_lint_detects_non_null_addfield_without_default(tmp_path):
    migration = _write_migration(
        tmp_path,
        "apps/catalog/migrations/0003_add_slug_pr8835.py",
        "from django.db import migrations, models\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = []\n"
        "    operations = [\n"
        "        migrations.AddField(\n"
        "            model_name='widget',\n"
        "            name='slug',\n"
        "            field=models.CharField(max_length=64),\n"
        "        )\n"
        "    ]\n",
    )

    findings = [
        finding.as_payload(repo_root=tmp_path)
        for finding in checks.lint_migration_file(migration, repo_root=tmp_path)
    ]

    assert findings == [
        {
            "code": "non-null-addfield-without-default",
            "line": 6,
            "message": (
                "AddField appears to add a non-null field without default, "
                "db_default, null=True, or detectable backfill evidence."
            ),
            "path": "apps/catalog/migrations/0003_add_slug_pr8835.py",
            "severity": "error",
        }
    ]


def test_migration_impact_report_lists_operations_and_seed_impact(
    monkeypatch, tmp_path
):
    migration_path = Path("apps/catalog/migrations/0002_seed_widgets_pr8835.py")
    _write_migration(
        tmp_path,
        migration_path.as_posix(),
        "from django.db import migrations\n\n"
        "def seed_widgets(apps, schema_editor):\n"
        "    pass\n\n"
        "def unseed_widgets(apps, schema_editor):\n"
        "    pass\n\n"
        "class Migration(migrations.Migration):\n"
        "    dependencies = [('core', '0001_initial')]\n"
        "    operations = [\n"
        "        migrations.RunPython(seed_widgets, reverse_code=unseed_widgets)\n"
        "    ]\n",
    )
    fixture_path = Path("apps/catalog/fixtures/widgets.json")
    (tmp_path / fixture_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / fixture_path).write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(
        checks,
        "_git_changed_migration_paths",
        lambda _repo_root, *, base_ref=None: [migration_path],
    )
    monkeypatch.setattr(
        checks,
        "_git_changed_paths",
        lambda _repo_root, *, base_ref=None, pathspecs=(): [
            migration_path,
            fixture_path,
        ],
    )
    monkeypatch.setattr(checks, "_git_ref", lambda _repo_root, _ref: "abc123")

    report = checks.build_migration_impact_report(tmp_path, base_ref="origin/main")

    assert report["risk"]["level"] == "medium"
    assert report["summary"]["fixture_or_seed_files"] == [fixture_path.as_posix()]
    assert report["operation_classes"] == ["RunPython"]
    assert report["migration_files"][0]["cross_app_dependencies"] == [
        {"app_label": "core", "migration_name": "0001_initial"}
    ]
    assert report["migration_files"][0]["seed_data_impact"] is True
    assert report["static_lint"] == []
    assert "RunPython" in checks.format_migration_impact_markdown(report)
