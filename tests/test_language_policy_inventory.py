from __future__ import annotations

from pathlib import Path

from scripts import language_policy_inventory as inventory


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_inventory_flags_non_english_docs_and_source_adjacent_prose(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "The operator documentation should explain the release workflow.\n",
    )
    _write(
        tmp_path / "apps" / "sample" / "README.md",
        "The nested README should also be inventoried.\n",
    )
    _write(
        tmp_path / "docs" / "policy.md",
        (
            "The documentation policy keeps contributors aligned.\n\n"
            "La politica de documentacion mantiene alineados a los operadores.\n"
        ),
    )
    _write(
        tmp_path / "apps" / "sample" / "models.py",
        '"""Operator workflow helper for release documentation."""\n',
    )
    _write(
        tmp_path / "apps" / "sample" / "views.py",
        '"""La vista prepara el flujo para operadores."""\n',
    )
    _write(
        tmp_path / "apps" / "sample" / "fixtures" / "external.json",
        '{"label": "La tarifa publica conserva datos externos."}\n',
    )
    _write(
        tmp_path / "apps" / "sample" / "templates" / "sample" / "es.json",
        '{"title": "Vista de operador"}\n',
    )
    _write(
        tmp_path / "apps" / "sample" / "static" / "htmx" / "extension.js",
        "/* La extension conserva el texto de origen. */\n",
    )
    _write(tmp_path / "apps" / "sample" / "constants.py", "MAX_RETRIES = 3\n")

    report = inventory.build_inventory(tmp_path)
    entries = {entry.path: entry for entry in report.entries}

    assert entries["README.md"].status == "english"
    assert entries["apps/sample/README.md"].status == "english"
    assert entries["docs/policy.md"].status == "english"
    assert entries["apps/sample/models.py"].status == "english"
    assert entries["apps/sample/views.py"].status == (
        "source-adjacent-needs-english-review"
    )
    assert entries["apps/sample/fixtures/external.json"].status == "preserve"
    assert entries["apps/sample/templates/sample/es.json"].status == "preserve"
    assert entries["apps/sample/static/htmx/extension.js"].status == "preserve"
    assert entries["apps/sample/constants.py"].status == "preserve"


def test_markdown_report_summarizes_policy_gaps(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "La politica debe explicar el flujo para operadores.\n")

    report = inventory.build_inventory(tmp_path)
    markdown = inventory.format_markdown(report, limit=10)

    assert "# Language Policy Inventory" in markdown
    assert "- README/docs missing English: 1" in markdown
    assert "`README.md`: missing-english" in markdown


def test_inventory_skips_symlinks_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-policy.md"
    outside.write_text("The external document should not be scanned.\n", encoding="utf-8")
    link_path = tmp_path / "docs" / "external.md"
    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(outside)

    report = inventory.build_inventory(tmp_path)

    assert "docs/external.md" not in {entry.path for entry in report.entries}
