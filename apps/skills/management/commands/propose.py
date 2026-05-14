from __future__ import annotations

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.skills.codex_wrapper import run_codex_with_prompt_hooks


class Command(BaseCommand):
    help = "Run before_prompt hooks and then launch Codex with the allowed prompt."

    def add_arguments(self, parser):
        parser.add_argument(
            "prompt_args",
            nargs="*",
            help="Prompt text. Use --prompt, --prompt-file, or --stdin for explicit input.",
        )
        parser.add_argument("--prompt", help="Prompt text to guard and pass to Codex.")
        parser.add_argument("--prompt-file", help="Read prompt text from a UTF-8 file.")
        parser.add_argument("--stdin", action="store_true", help="Read prompt text from stdin.")
        parser.add_argument(
            "--codex-command",
            default="codex",
            help="Codex executable command. Default: codex.",
        )
        parser.add_argument(
            "--source",
            default="cli",
            help="Prompt source label passed to before_prompt hooks. Default: cli.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run hooks and report the decision without launching Codex.",
        )
        parser.add_argument(
            "--fail-open",
            action="store_true",
            help="Continue when a before_prompt hook errors. Default: fail closed.",
        )
        parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    def handle(self, *args, **options):
        prompt = self._read_prompt(options)
        result = run_codex_with_prompt_hooks(
            prompt,
            codex_command=options["codex_command"],
            source=options["source"],
            fail_open=options["fail_open"],
            dry_run=options["dry_run"],
        )

        if options["json"]:
            self.stdout.write(json.dumps(result.as_dict(), indent=2))
        elif options["dry_run"] or not result.guard.should_launch:
            self._write_text_result(result)

        if not result.guard.should_launch and not options["dry_run"]:
            raise CommandError(result.guard.reason or "Prompt refused by before_prompt hooks.")
        if result.return_code not in {None, 0}:
            raise CommandError(f"Codex exited with status {result.return_code}.")
        return None

    def _read_prompt(self, options) -> str:
        sources = [
            bool(options["prompt_args"]),
            options["prompt"] is not None,
            options["prompt_file"] is not None,
            bool(options["stdin"]),
        ]
        if sum(sources) != 1:
            raise CommandError("Provide exactly one prompt source.")
        if options["prompt_args"]:
            return " ".join(options["prompt_args"])
        if options["prompt"] is not None:
            return options["prompt"]
        if options["prompt_file"] is not None:
            return Path(options["prompt_file"]).read_text(encoding="utf-8")
        return sys.stdin.read()

    def _write_text_result(self, result) -> None:
        self.stdout.write(f"status={result.guard.status}")
        self.stdout.write(f"should_launch={str(result.guard.should_launch).lower()}")
        self.stdout.write(f"hooks={len(result.guard.hooks)}")
        if result.guard.refused_by:
            self.stdout.write(f"refused_by={result.guard.refused_by}")
        if result.guard.reason:
            self.stdout.write(f"reason={result.guard.reason}")
        if result.guard.status == "rewrite":
            self.stdout.write("prompt:")
            self.stdout.write(result.guard.prompt)
