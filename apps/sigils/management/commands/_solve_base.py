"""Shared management command implementation for solve/resolve aliases."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.sigils.script_runtime import (
    ScriptParseError,
    ScriptPolicyError,
    ScriptRuntimeError,
    execute_script_text,
)


class BaseSolveCommand(BaseCommand):
    """Run sigil expressions and deterministic scripts using shared resolver semantics."""

    default_use_cache = False

    def add_arguments(self, parser) -> None:
        parser.add_argument("--expr", help="Inline expression to evaluate.")
        parser.add_argument("--file", help="Path to a .artx script file.")
        parser.add_argument(
            "--context",
            default="admin",
            choices=["admin", "request", "user"],
            help="Resolver policy context.",
        )
        parser.add_argument(
            "--output",
            default="text",
            choices=["json", "text"],
            help="Output format.",
        )
        parser.add_argument(
            "--cache",
            action="store_true",
            help="Enable script-result caching for identical inputs.",
        )
        parser.add_argument(
            "--no-cache",
            action="store_true",
            help="Disable script-result caching for this run.",
        )

    def handle(self, *args, **options) -> None:
        expr = options.get("expr")
        file_path = options.get("file")
        context = options["context"]
        output_mode = options["output"]
        use_cache = self._resolve_cache_option(options)

        if bool(expr) == bool(file_path):
            raise CommandError("Provide exactly one input: --expr or --file.")

        try:
            if expr:
                script_text = f"EMIT {expr}"
            else:
                script_text = self._load_script(file_path)

            outputs = execute_script_text(
                script_text,
                context=context,
                use_cache=use_cache,
            )
        except ScriptParseError as exc:
            raise CommandError(f"parse error: {exc}") from exc
        except ScriptPolicyError as exc:
            raise CommandError(f"policy error: {exc}") from exc
        except ScriptRuntimeError as exc:
            raise CommandError(f"runtime error: {exc}") from exc

        if output_mode == "json":
            payload = {
                "context": context,
                "outputs": outputs,
            }
            self.stdout.write(json.dumps(payload))
            return

        for item in outputs:
            self.stdout.write(item)

    def _resolve_cache_option(self, options: dict) -> bool:
        if options["cache"] and options["no_cache"]:
            raise CommandError("Use only one cache switch: --cache or --no-cache.")
        if options["cache"]:
            return True
        if options["no_cache"]:
            return False
        return self.default_use_cache

    def _load_script(self, file_path: str | None) -> str:
        if not file_path:
            raise CommandError("--file is required when --expr is not provided.")
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise CommandError(f"Script file not found: {file_path}")
        if path.suffix.lower() != ".artx":
            raise CommandError("Script file must use the .artx extension.")
        return path.read_text(encoding="utf-8")
