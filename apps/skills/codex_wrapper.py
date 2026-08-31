from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from django.conf import settings

from apps.skills.prompt_hooks import (
    PromptGuardBlocked,
    PromptGuardOutcome,
    require_prompt_allowed,
    run_before_prompt_hooks,
    split_command_line,
)


@dataclass(frozen=True)
class CodexWrapperResult:
    guard: PromptGuardOutcome
    command: list[str]
    launched: bool
    return_code: int | None = None

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["guard"] = self.guard.as_dict()
        return payload


def build_codex_command(prompt: str, *, codex_command: str = "codex") -> list[str]:
    command = split_command_line(codex_command.strip() or "codex")
    command.append(prompt)
    return command


def prepare_codex_prompt(
    prompt: str,
    *,
    source: str = "cli",
    metadata: Mapping[str, object] | None = None,
    fail_open: bool = False,
) -> str:
    outcome = require_prompt_allowed(
        prompt,
        source=source,
        metadata=metadata,
        fail_open=fail_open,
    )
    return outcome.prompt


def run_codex_with_prompt_hooks(
    prompt: str,
    *,
    codex_command: str = "codex",
    source: str = "cli",
    metadata: Mapping[str, object] | None = None,
    fail_open: bool = False,
    dry_run: bool = False,
    cwd: Path | str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> CodexWrapperResult:
    runner = runner or subprocess.run
    guard = run_before_prompt_hooks(
        prompt,
        source=source,
        metadata=metadata,
        fail_open=fail_open,
        runner=runner,
    )
    command = build_codex_command(guard.prompt, codex_command=codex_command)
    if not guard.should_launch or dry_run:
        return CodexWrapperResult(
            guard=guard,
            command=command,
            launched=False,
        )

    completed = runner(
        command,
        cwd=str(cwd or settings.BASE_DIR),
        env=dict(os.environ),
        check=False,
    )
    return CodexWrapperResult(
        guard=guard,
        command=command,
        launched=True,
        return_code=completed.returncode,
    )


__all__ = [
    "CodexWrapperResult",
    "PromptGuardBlocked",
    "build_codex_command",
    "prepare_codex_prompt",
    "run_codex_with_prompt_hooks",
]
