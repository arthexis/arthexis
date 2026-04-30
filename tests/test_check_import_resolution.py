from __future__ import annotations

from scripts import check_import_resolution


def test_module_not_found_optional_marker_skips_missing_import(tmp_path) -> None:
    module_path = tmp_path / "optional_module.py"
    module_path.write_text(
        "\n".join(
            [
                "try:",
                "    'optional-import'",
                "    import definitely_missing_arthexis_optional_module",
                "except ModuleNotFoundError:",
                "    definitely_missing_arthexis_optional_module = None",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert check_import_resolution.collect_missing_imports([module_path]) == []


def test_module_not_found_without_marker_still_reports_missing_import(
    tmp_path,
) -> None:
    module_path = tmp_path / "missing_module.py"
    module_path.write_text(
        "\n".join(
            [
                "try:",
                "    import definitely_missing_arthexis_required_module",
                "except ModuleNotFoundError:",
                "    definitely_missing_arthexis_required_module = None",
                "",
            ]
        ),
        encoding="utf-8",
    )

    issues = check_import_resolution.collect_missing_imports([module_path])

    assert len(issues) == 1
    assert issues[0].module == "definitely_missing_arthexis_required_module"
