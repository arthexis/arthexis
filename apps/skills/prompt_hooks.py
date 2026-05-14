from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from django.conf import settings

from apps.sigils.sigil_resolver import resolve_sigils
from apps.skills.hook_context import current_hook_platform, list_hooks
from apps.skills.models import Hook

ALLOW_DECISION = "allow"
REFUSE_DECISION = "refuse"
REWRITE_DECISION = "rewrite"
ERROR_DECISION = "error"

ALLOWED_PROMPT_DECISIONS = frozenset(
    {ALLOW_DECISION, REFUSE_DECISION, REWRITE_DECISION, ERROR_DECISION}
)


@dataclass(frozen=True)
class PromptHookStep:
    slug: str
    title: str
    decision: str
    reason: str = ""
    return_code: int | None = None
    elapsed_seconds: float = 0.0
    stderr: str = ""


@dataclass(frozen=True)
class PromptGuardOutcome:
    status: str
    prompt: str
    original_prompt: str
    source: str
    hooks: list[PromptHookStep]
    reason: str = ""
    refused_by: str = ""
    errors: list[str] | None = None

    @property
    def should_launch(self) -> bool:
        return self.status in {ALLOW_DECISION, REWRITE_DECISION}

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["should_launch"] = self.should_launch
        return payload


class PromptGuardBlocked(RuntimeError):
    def __init__(self, outcome: PromptGuardOutcome):
        self.outcome = outcome
        super().__init__(outcome.reason or "Prompt guard blocked the Codex prompt.")


def split_command_line(value: str) -> list[str]:
    parts = shlex.split(value, posix=sys.platform != "win32")
    if sys.platform == "win32":
        return [part.strip('"') for part in parts]
    return parts


def run_before_prompt_hooks(
    prompt: str,
    *,
    source: str = "cli",
    metadata: Mapping[str, object] | None = None,
    platform: str | None = None,
    fail_open: bool = False,
    hooks: Sequence[Mapping[str, object]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> PromptGuardOutcome:
    runner = runner or subprocess.run
    selected_platform = platform or current_hook_platform()
    hook_records = list(hooks) if hooks is not None else list_hooks(
        event=Hook.Event.BEFORE_PROMPT,
        platform=selected_platform,
    )
    current_prompt = prompt
    steps: list[PromptHookStep] = []
    errors: list[str] = []
    rewritten = False
    base_metadata = _base_prompt_metadata(selected_platform)
    if metadata:
        base_metadata.update({str(key): value for key, value in metadata.items()})

    for hook in hook_records:
        payload = {
            "event": Hook.Event.BEFORE_PROMPT,
            "prompt": current_prompt,
            "source": source,
            "metadata": base_metadata,
            "hook": {
                "slug": str(hook["slug"]),
                "title": str(hook["title"]),
            },
        }
        step, hook_output = _run_prompt_hook(hook, payload, runner=runner)
        steps.append(step)
        if step.decision == ERROR_DECISION:
            errors.append(step.reason)
            if fail_open:
                continue
            return PromptGuardOutcome(
                status=ERROR_DECISION,
                prompt=current_prompt,
                original_prompt=prompt,
                source=source,
                hooks=steps,
                reason=step.reason,
                refused_by=str(hook["slug"]),
                errors=errors,
            )
        decision = step.decision
        if decision == ALLOW_DECISION:
            continue
        if decision == REWRITE_DECISION:
            rewritten_prompt = hook_output.get("prompt")
            if not isinstance(rewritten_prompt, str):
                reason = f"before_prompt hook {hook['slug']} returned rewrite without a string prompt."
                errors.append(reason)
                error_step = PromptHookStep(
                    slug=str(hook["slug"]),
                    title=str(hook["title"]),
                    decision=ERROR_DECISION,
                    reason=reason,
                )
                steps[-1] = error_step
                if fail_open:
                    continue
                return PromptGuardOutcome(
                    status=ERROR_DECISION,
                    prompt=current_prompt,
                    original_prompt=prompt,
                    source=source,
                    hooks=steps,
                    reason=reason,
                    refused_by=str(hook["slug"]),
                    errors=errors,
                )
            rewritten = rewritten or rewritten_prompt != current_prompt
            current_prompt = rewritten_prompt
            continue
        if decision == REFUSE_DECISION:
            reason = str(hook_output.get("reason") or "Prompt refused by before_prompt hook.")
            return PromptGuardOutcome(
                status=REFUSE_DECISION,
                prompt=current_prompt,
                original_prompt=prompt,
                source=source,
                hooks=steps,
                reason=reason,
                refused_by=str(hook["slug"]),
                errors=errors,
            )

    return PromptGuardOutcome(
        status=REWRITE_DECISION if rewritten else ALLOW_DECISION,
        prompt=current_prompt,
        original_prompt=prompt,
        source=source,
        hooks=steps,
        errors=errors,
    )


def require_prompt_allowed(
    prompt: str,
    *,
    source: str = "cli",
    metadata: Mapping[str, object] | None = None,
    platform: str | None = None,
    fail_open: bool = False,
) -> PromptGuardOutcome:
    outcome = run_before_prompt_hooks(
        prompt,
        source=source,
        metadata=metadata,
        platform=platform,
        fail_open=fail_open,
    )
    if not outcome.should_launch:
        raise PromptGuardBlocked(outcome)
    return outcome


def _base_prompt_metadata(platform: str) -> dict[str, object]:
    return {
        "base_dir": str(settings.BASE_DIR),
        "cwd": str(Path.cwd()),
        "platform": platform,
    }


def _run_prompt_hook(
    hook: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[PromptHookStep, dict[str, object]]:
    started = time.monotonic()
    hook_slug = str(hook["slug"])
    hook_title = str(hook["title"])
    try:
        serialized_payload = json.dumps(payload)
        completed = runner(
            split_command_line(_resolve_runtime_text(str(hook["command"]))),
            input=serialized_payload,
            text=True,
            capture_output=True,
            timeout=int(hook["timeout_seconds"]),
            cwd=_hook_cwd(hook),
            env=_hook_env(hook),
            check=False,
        )
    except (TypeError, ValueError) as exc:
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} payload serialization failed: {exc}",
                elapsed_seconds=time.monotonic() - started,
            ),
            {},
        )
    except subprocess.TimeoutExpired as exc:
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} timed out after {hook['timeout_seconds']} seconds.",
                elapsed_seconds=time.monotonic() - started,
                stderr=str(exc.stderr or ""),
            ),
            {},
        )
    except (OSError, ValueError) as exc:
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} failed to start: {exc}",
                elapsed_seconds=time.monotonic() - started,
            ),
            {},
        )

    elapsed = time.monotonic() - started
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or f"exit status {completed.returncode}"
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} failed: {detail}",
                return_code=completed.returncode,
                elapsed_seconds=elapsed,
                stderr=stderr,
            ),
            {},
        )

    try:
        hook_output = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} returned invalid JSON: {exc.msg}",
                return_code=completed.returncode,
                elapsed_seconds=elapsed,
                stderr=stderr,
            ),
            {},
        )
    if not isinstance(hook_output, dict):
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} returned a non-object JSON payload.",
                return_code=completed.returncode,
                elapsed_seconds=elapsed,
                stderr=stderr,
            ),
            {},
        )
    decision = str(hook_output.get("decision", "")).strip().lower()
    if decision not in ALLOWED_PROMPT_DECISIONS:
        return (
            PromptHookStep(
                slug=hook_slug,
                title=hook_title,
                decision=ERROR_DECISION,
                reason=f"before_prompt hook {hook_slug} returned unsupported decision: {decision or '<blank>'}.",
                return_code=completed.returncode,
                elapsed_seconds=elapsed,
                stderr=stderr,
            ),
            {},
        )
    return (
        PromptHookStep(
            slug=hook_slug,
            title=hook_title,
            decision=decision,
            reason=str(hook_output.get("reason") or ""),
            return_code=completed.returncode,
            elapsed_seconds=elapsed,
            stderr=stderr,
        ),
        hook_output,
    )


def _hook_cwd(hook: Mapping[str, object]) -> str:
    working_directory = str(hook.get("working_directory") or "").strip()
    if not working_directory:
        return str(settings.BASE_DIR)
    return _resolve_runtime_text(working_directory)


def _hook_env(hook: Mapping[str, object]) -> dict[str, str]:
    environment = hook.get("environment") or {}
    resolved = dict(os.environ)
    if not isinstance(environment, Mapping):
        return resolved
    for key, value in environment.items():
        if value is None:
            continue
        resolved[str(key)] = _resolve_runtime_text(str(value))
    return resolved


def _resolve_runtime_text(value: str) -> str:
    return resolve_sigils(value, current=None)
