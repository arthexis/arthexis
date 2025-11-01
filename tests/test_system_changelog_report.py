from __future__ import annotations

import pytest

from core import system


@pytest.fixture(autouse=True)
def reset_github_commit_url_cache():
    system._github_commit_url_base.cache_clear()  # noqa: SLF001
    try:
        yield
    finally:
        system._github_commit_url_base.cache_clear()  # noqa: SLF001


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


def test_open_changelog_entries_include_github_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    changelog_text = (
        "Changelog\n"
        "=========\n\n"
        "Unreleased\n"
        "----------\n\n"
        "- abc12345 First change\n\n"
    )
    (tmp_path / "CHANGELOG.rst").write_text(changelog_text, encoding="utf-8")

    monkeypatch.setattr(
        system,
        "_git_remote_url",
        lambda remote="origin": "https://github.com/example/project.git",
    )

    entries = system._open_changelog_entries()  # noqa: SLF001

    assert entries
    assert entries[0]["url"] == "https://github.com/example/project/commit/abc12345"


def test_latest_release_changelog_includes_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    changelog_text = (
        "Changelog\n"
        "=========\n\n"
        "Unreleased\n"
        "----------\n\n"
        "1.0.0 - 2023-01-01\n"
        "------------------\n"
        "- def67890 Second change\n\n"
    )
    (tmp_path / "CHANGELOG.rst").write_text(changelog_text, encoding="utf-8")

    monkeypatch.setattr(
        system,
        "_git_remote_url",
        lambda remote="origin": "git@github.com:example/project.git",
    )

    release = system._latest_release_changelog()  # noqa: SLF001

    assert release["entries"][0]["url"] == "https://github.com/example/project/commit/def67890"


def test_github_commit_url_base_handles_missing_git(monkeypatch):
    def raise_file_not_found(remote="origin"):
        raise FileNotFoundError

    monkeypatch.setattr(system, "_git_remote_url", raise_file_not_found)

    url = system._github_commit_url("abc12345")  # noqa: SLF001

    assert url == ""
