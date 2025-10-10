from __future__ import annotations

from core import system


def test_exclude_changelog_entries_removes_selected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    changelog_text = (
        "Changelog\n"
        "=========\n\n"
        "Unreleased\n"
        "----------\n\n"
        "- abc123 First change\n"
        "- def456 Second change\n\n"
        "1.0.0 - 2023-01-01\n"
        "------------------\n"
    )
    (tmp_path / "CHANGELOG.rst").write_text(changelog_text, encoding="utf-8")

    removed = system._exclude_changelog_entries(["abc123"])  # noqa: SLF001

    assert removed == 1
    updated = (tmp_path / "CHANGELOG.rst").read_text(encoding="utf-8")
    assert "- abc123" not in updated
    assert "- def456 Second change" in updated


def test_exclude_changelog_entries_ignores_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    changelog_text = (
        "Changelog\n"
        "=========\n\n"
        "Unreleased\n"
        "----------\n\n"
        "- abc123 First change\n\n"
        "1.0.0 - 2023-01-01\n"
        "------------------\n"
    )
    (tmp_path / "CHANGELOG.rst").write_text(changelog_text, encoding="utf-8")

    removed = system._exclude_changelog_entries(["zzz999"])  # noqa: SLF001

    assert removed == 0
    updated = (tmp_path / "CHANGELOG.rst").read_text(encoding="utf-8")
    assert updated == changelog_text
