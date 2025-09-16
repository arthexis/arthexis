from __future__ import annotations

from pathlib import Path

from utils.git_checks import find_nested_git_repositories


def test_repository_has_no_nested_git_repositories() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    nested = find_nested_git_repositories(repo_root)
    assert (
        not nested
    ), f"Nested git repositories detected: {', '.join(str(path) for path in nested)}"


def test_detects_nested_git_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    nested = tmp_path / "vendor" / "lib"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()

    assert find_nested_git_repositories(tmp_path) == [Path("vendor/lib")]


def test_detects_gitlink_nested_repository(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /tmp/worktrees/main\n")

    nested = tmp_path / "external"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: ../.git/modules/external\n")

    assert find_nested_git_repositories(tmp_path) == [Path("external")]
