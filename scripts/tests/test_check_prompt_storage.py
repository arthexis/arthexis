"""Regression tests for stored prompt pre-commit validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_prompt_storage


@pytest.fixture
def fixture_payload() -> list[dict]:
    """Return a valid prompts fixture payload."""

    return [
        {
            "model": "prompts.storedprompt",
            "fields": {
                "prompt_text": "original prompt",
                "initial_plan": "refined plan",
                "pr_reference": "PR-42",
                "context": {"files": ["apps/prompts/models.py"]},
            },
        }
    ]


def test_main_fails_without_prompt_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: non-fixture changes must include a prompts fixture update."""

    monkeypatch.setattr(
        check_prompt_storage, "_staged_files", lambda: [Path("apps/core/models.py")]
    )

    assert check_prompt_storage.main() == 1


def test_main_requires_fixture_for_deleted_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: deleted/renamed code still requires prompt fixture updates."""

    monkeypatch.setattr(
        check_prompt_storage, "_staged_files", lambda: [Path("apps/core/obsolete.py")]
    )

    assert check_prompt_storage.main() == 1


def test_main_accepts_when_fixture_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_payload: list[dict]
) -> None:
    """Regression: staged prompt fixture with required fields passes validation."""

    fixture_path = tmp_path / "apps" / "prompts" / "fixtures" / "prompts__sample.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps(fixture_payload), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        check_prompt_storage,
        "_staged_files",
        lambda: [
            Path("apps/core/models.py"),
            Path("apps/prompts/fixtures/prompts__sample.json"),
        ],
    )

    assert check_prompt_storage.main() == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_plan", ""),
        ("initial_plan", "   "),
        ("prompt_text", ""),
        ("pr_reference", ""),
        ("context", {}),
        ("context", "not-an-object"),
    ],
)
def test_validate_fixture_rejects_invalid_required_values(
    tmp_path: Path, fixture_payload: list[dict], field: str, value: object
) -> None:
    """Regression: required values must be present and non-empty."""

    fixture_payload[0]["fields"][field] = value
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(fixture_payload), encoding="utf-8")

    with pytest.raises(check_prompt_storage.PromptStorageError):
        check_prompt_storage._validate_fixture(path)
