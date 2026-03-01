from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

import scripts.lint_seed_fixtures as lint_seed_fixtures
from scripts.lint_seed_fixtures import find_missing_seed_flags


@pytest.mark.django_db
def test_seed_fixtures_include_seed_flag() -> None:
    fixtures_root = Path(settings.BASE_DIR) / "apps"

    assert find_missing_seed_flags(fixtures_root) == []


def test_resolve_fixture_paths_accepts_relative_and_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixtures" / "fixture.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("[]")

    monkeypatch.setattr(lint_seed_fixtures, "REPO_ROOT", tmp_path)

    resolved = lint_seed_fixtures._resolve_fixture_paths([
        str(Path("fixtures") / "fixture.json"),
        str(fixture),
    ])

    assert resolved == [fixture.resolve(), fixture.resolve()]


def test_resolve_fixture_paths_raises_for_missing_file() -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        lint_seed_fixtures._resolve_fixture_paths(["missing/fixture.json"])


def test_main_uses_explicit_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("[]")

    captured: dict[str, object] = {}

    def fake_find_missing_seed_flags(
        fixtures_root: Path,
        fixture_paths: list[Path] | None = None,
    ) -> list[tuple[Path, str]]:
        captured["fixtures_root"] = fixtures_root
        captured["fixture_paths"] = fixture_paths
        return []

    monkeypatch.setattr(
        lint_seed_fixtures,
        "find_missing_seed_flags",
        fake_find_missing_seed_flags,
    )

    exit_code = lint_seed_fixtures.main([str(fixture)])

    assert exit_code == 0
    assert captured["fixture_paths"] == [fixture.resolve()]


def test_main_defaults_to_full_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_find_missing_seed_flags(
        fixtures_root: Path,
        fixture_paths: list[Path] | None = None,
    ) -> list[tuple[Path, str]]:
        captured["fixtures_root"] = fixtures_root
        captured["fixture_paths"] = fixture_paths
        return []

    monkeypatch.setattr(
        lint_seed_fixtures,
        "find_missing_seed_flags",
        fake_find_missing_seed_flags,
    )

    exit_code = lint_seed_fixtures.main([])

    assert exit_code == 0
    assert captured["fixture_paths"] is None
