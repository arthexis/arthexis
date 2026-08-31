from __future__ import annotations

import json
import subprocess
from io import StringIO

import pytest
from django.core.management import call_command

from apps.skills.codex_wrapper import run_codex_with_prompt_hooks
from apps.skills.models import Hook
from apps.skills.prompt_hooks import run_before_prompt_hooks

pytestmark = [pytest.mark.django_db]


class PromptHookRunner:
    def __init__(self, responses: dict[str, dict[str, object] | str]):
        self.responses = responses
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        response = self.responses[command[0]]
        if isinstance(response, str):
            stdout = response
        else:
            stdout = json.dumps(response)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_before_prompt_hooks_rewrite_then_refuse():
    Hook.objects.create(
        slug="rewrite-prompt",
        title="Rewrite prompt",
        event=Hook.Event.BEFORE_PROMPT,
        command="rewrite-hook",
        priority=10,
    )
    Hook.objects.create(
        slug="refuse-prompt",
        title="Refuse prompt",
        event=Hook.Event.BEFORE_PROMPT,
        command="refuse-hook",
        priority=20,
    )
    runner = PromptHookRunner(
        {
            "rewrite-hook": {"decision": "rewrite", "prompt": "rewritten prompt"},
            "refuse-hook": {"decision": "refuse", "reason": "blocked keyword"},
        }
    )

    outcome = run_before_prompt_hooks("raw prompt", runner=runner)

    assert outcome.status == "refuse"
    assert outcome.prompt == "rewritten prompt"
    assert outcome.refused_by == "refuse-prompt"
    assert outcome.reason == "blocked keyword"
    assert json.loads(runner.calls[0][1]["input"])["prompt"] == "raw prompt"
    assert json.loads(runner.calls[1][1]["input"])["prompt"] == "rewritten prompt"


def test_before_prompt_hook_invalid_json_fails_closed():
    Hook.objects.create(
        slug="broken-prompt",
        title="Broken prompt",
        event=Hook.Event.BEFORE_PROMPT,
        command="broken-hook",
    )
    runner = PromptHookRunner({"broken-hook": "not-json"})

    outcome = run_before_prompt_hooks("raw prompt", runner=runner)

    assert outcome.status == "error"
    assert outcome.should_launch is False
    assert "invalid JSON" in outcome.reason


def test_codex_wrapper_launches_with_rewritten_prompt():
    Hook.objects.create(
        slug="rewrite-prompt",
        title="Rewrite prompt",
        event=Hook.Event.BEFORE_PROMPT,
        command="rewrite-hook",
    )
    calls = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        if "input" in kwargs:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {"decision": "rewrite", "prompt": "rewritten prompt"}
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0)

    result = run_codex_with_prompt_hooks(
        "raw prompt",
        codex_command="codex --model test",
        runner=runner,
    )

    assert result.guard.status == "rewrite"
    assert result.command == ["codex", "--model", "test", "rewritten prompt"]
    assert result.launched is True
    assert calls[-1][0] == result.command


def test_propose_dry_run_json_reports_allowed_prompt():
    stdout = StringIO()

    call_command(
        "propose",
        "--prompt",
        "hello",
        "--dry-run",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["guard"]["status"] == "allow"
    assert payload["guard"]["should_launch"] is True
    assert payload["launched"] is False
