from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.summary.management.commands import chat as chat_command


class FakeConfig:
    pass


class FakeSummarizer:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def summarize(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"reply-{len(self.prompts)}"


def test_chat_command_sends_one_shot_message_through_summary_summarizer(
    monkeypatch, tmp_path
) -> None:
    fake_summarizer = FakeSummarizer()
    monkeypatch.setattr(chat_command, "get_summary_config", lambda: FakeConfig())
    monkeypatch.setattr(chat_command, "ensure_local_model", lambda config: tmp_path)
    monkeypatch.setattr(chat_command, "LocalLLMSummarizer", lambda: fake_summarizer)

    stdout = StringIO()
    call_command("chat", message=["status?"], raw=True, stdout=stdout)

    assert stdout.getvalue().strip() == "reply-1"
    assert "CHAT TRANSCRIPT:" in fake_summarizer.prompts[0]
    assert "operator: status?" in fake_summarizer.prompts[0]
    assert "LOGS:\nstatus?" in fake_summarizer.prompts[0]


def test_chat_command_reuses_history_for_repeated_messages(monkeypatch, tmp_path) -> None:
    fake_summarizer = FakeSummarizer()
    monkeypatch.setattr(chat_command, "get_summary_config", lambda: FakeConfig())
    monkeypatch.setattr(chat_command, "ensure_local_model", lambda config: tmp_path)
    monkeypatch.setattr(chat_command, "LocalLLMSummarizer", lambda: fake_summarizer)

    stdout = StringIO()
    call_command("chat", message=["first", "second"], raw=True, stdout=stdout)

    assert stdout.getvalue().splitlines() == ["reply-1", "reply-2"]
    assert "operator: first" in fake_summarizer.prompts[1]
    assert "assistant: reply-1" in fake_summarizer.prompts[1]
    assert "operator: second" in fake_summarizer.prompts[1]


def test_chat_command_raw_requires_message_before_model_setup(monkeypatch) -> None:
    def fail_model_setup() -> None:
        raise AssertionError("raw validation should run before model setup")

    monkeypatch.setattr(chat_command, "get_summary_config", fail_model_setup)

    try:
        call_command("chat", raw=True)
    except CommandError as exc:
        assert "--raw requires at least one --message" in str(exc)
    else:
        raise AssertionError("Expected CommandError")
