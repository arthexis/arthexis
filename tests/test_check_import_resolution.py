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


def test_module_not_found_optional_marker_skips_missing_import_tuple_exception(
    tmp_path,
) -> None:
    module_path = tmp_path / "optional_tuple_module.py"
    module_path.write_text(
        "\n".join(
            [
                "try:",
                "    'optional-import'",
                "    import definitely_missing_arthexis_optional_tuple_module",
                "except (ModuleNotFoundError, ImportError):",
                "    definitely_missing_arthexis_optional_tuple_module = None",
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


def test_qr_printing_windows_registry_import_is_optional() -> None:
    module_path = (
        check_import_resolution.PROJECT_ROOT / "apps" / "links" / "qr_printing.py"
    )

    issues = check_import_resolution.collect_missing_imports([module_path])

    assert [issue for issue in issues if issue.module == "winreg"] == []


def test_lcd_replay_posix_terminal_imports_are_optional() -> None:
    module_path = (
        check_import_resolution.PROJECT_ROOT
        / "apps"
        / "screens"
        / "management"
        / "commands"
        / "lcd_actions"
        / "replay.py"
    )

    issues = check_import_resolution.collect_missing_imports([module_path])

    assert [issue for issue in issues if issue.module in {"termios", "tty"}] == []
