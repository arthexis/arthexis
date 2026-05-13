from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.summary.services import ensure_local_model, get_summary_config
from apps.tasks.tasks import LocalLLMSummarizer

DEFAULT_SYSTEM_PROMPT = (
    "You are the local Arthexis LCD summary model running in a management "
    "command smoke test. Answer the operator's latest message concisely."
)


def build_chat_prompt(
    history: list[tuple[str, str]], *, system_prompt: str = DEFAULT_SYSTEM_PROMPT
) -> str:
    """Build a prompt that works for both chat-style and LCD-summary adapters."""

    transcript_lines = [system_prompt.strip(), "", "CHAT TRANSCRIPT:"]
    log_lines = ["LOGS:"]
    for role, message in history:
        clean_message = " ".join(str(message or "").split())
        if not clean_message:
            continue
        transcript_lines.append(f"{role}: {clean_message}")
        log_lines.append(clean_message)
    transcript_lines.append("assistant:")
    return "\n".join([*transcript_lines, "", *log_lines, ""])


class Command(BaseCommand):
    """Run a small chat loop against the LCD summary local LLM adapter."""

    help = "Chat with the local LLM adapter used by LCD summary generation."

    def add_arguments(self, parser) -> None:
        """Register command-line flags."""

        parser.add_argument(
            "-m",
            "--message",
            action="append",
            default=None,
            help=(
                "Send a message and exit. Repeat to send a short transcript in "
                "one command invocation."
            ),
        )
        parser.add_argument(
            "--system-prompt",
            default=DEFAULT_SYSTEM_PROMPT,
            help="System instruction prepended to each chat prompt.",
        )
        parser.add_argument(
            "--raw",
            action="store_true",
            help="Print only model replies, without status lines or prompts.",
        )

    def handle(self, *args, **options) -> None:
        """Run one-shot messages or an interactive chat session."""

        messages = [
            text
            for message in (options.get("message") or [])
            if (text := str(message or "").strip())
        ]
        raw: bool = bool(options["raw"])
        if raw and not messages:
            raise CommandError("--raw requires at least one --message.")

        config = get_summary_config()
        model_path = ensure_local_model(config)
        summarizer = LocalLLMSummarizer()
        system_prompt: str = options["system_prompt"]

        if messages:
            self._run_messages(
                messages,
                summarizer=summarizer,
                model_path=model_path,
                system_prompt=system_prompt,
                raw=raw,
            )
            return

        self._run_interactive(
            summarizer=summarizer,
            model_path=model_path,
            system_prompt=system_prompt,
        )

    def _run_messages(
        self,
        messages: list[str],
        *,
        summarizer: LocalLLMSummarizer,
        model_path: Path,
        system_prompt: str,
        raw: bool,
    ) -> None:
        history: list[tuple[str, str]] = []
        if not raw:
            self._write_header(model_path)

        for message in messages:
            text = str(message or "").strip()
            history.append(("operator", text))
            reply = summarizer.summarize(
                build_chat_prompt(history, system_prompt=system_prompt)
            )
            history.append(("assistant", reply))
            if raw:
                self.stdout.write(reply)
            else:
                self.stdout.write(f"operator> {text}")
                self.stdout.write("assistant>")
                self.stdout.write(reply)

    def _run_interactive(
        self,
        *,
        summarizer: LocalLLMSummarizer,
        model_path: Path,
        system_prompt: str,
    ) -> None:
        self._write_header(model_path)
        self.stdout.write("Type :quit to exit, :reset to clear chat history.")
        history: list[tuple[str, str]] = []

        while True:
            try:
                message = input("operator> ")
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("")
                return
            text = message.strip()
            if not text:
                continue
            if text in {":q", ":quit", "quit", "exit"}:
                return
            if text == ":reset":
                history.clear()
                self.stdout.write("History reset.")
                continue

            history.append(("operator", text))
            reply = summarizer.summarize(
                build_chat_prompt(history, system_prompt=system_prompt)
            )
            history.append(("assistant", reply))
            self.stdout.write("assistant>")
            self.stdout.write(reply)

    def _write_header(self, model_path: Path) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Local LLM Chat"))
        self.stdout.write(f"Backend: {LocalLLMSummarizer.__name__}")
        self.stdout.write(f"Model path: {model_path}")
