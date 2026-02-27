#!/usr/bin/env python3
"""Ensure commits include a stored prompt fixture with implementation context."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROMPT_FIXTURE_DIR = Path("apps/prompts/fixtures")


class PromptStorageError(RuntimeError):
    """Raised when prompt storage validation fails."""


def _staged_files() -> list[Path]:
    """Return staged files, including adds/modifies/deletes/renames."""

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def _is_prompt_fixture(path: Path) -> bool:
    """Return whether the path points to a prompts fixture file."""

    return path.suffix == ".json" and PROMPT_FIXTURE_DIR in path.parents


def _validate_fixture(path: Path) -> None:
    """Validate prompts fixture content for required StoredPrompt fields."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptStorageError(f"{path}: invalid JSON ({exc})") from exc

    if not isinstance(payload, list):
        raise PromptStorageError(f"{path}: fixture root must be a list")

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("model") != "prompts.storedprompt":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            raise PromptStorageError(
                f"{path}: prompts.storedprompt entry must include fields"
            )
        required = ("prompt_text", "initial_plan", "pr_reference", "context")
        missing = [name for name in required if name not in fields]
        invalid_text = [
            name
            for name in ("prompt_text", "initial_plan", "pr_reference")
            if not isinstance(fields.get(name), str) or not fields[name].strip()
        ]
        if not isinstance(fields.get("context"), dict) or not fields["context"]:
            invalid_text.append("context")

        invalid = sorted(set(missing + invalid_text))
        if invalid:
            raise PromptStorageError(
                f"{path}: prompts.storedprompt missing required values: {', '.join(invalid)}"
            )
        return

    raise PromptStorageError(f"{path}: no prompts.storedprompt entry found")


def main() -> int:
    """Run staged-file prompt persistence validation."""

    staged = _staged_files()
    if not staged:
        return 0

    non_prompt_changes = [path for path in staged if not _is_prompt_fixture(path)]
    if not non_prompt_changes:
        return 0

    prompt_fixtures = [path for path in staged if _is_prompt_fixture(path)]
    if not prompt_fixtures:
        print(
            "Missing prompts fixture update: stage a JSON file under apps/prompts/fixtures ",
            "with prompts.storedprompt including prompt_text, initial_plan, pr_reference, and context.",
            file=sys.stderr,
            sep="",
        )
        return 1

    try:
        for path in prompt_fixtures:
            _validate_fixture(path)
    except PromptStorageError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
