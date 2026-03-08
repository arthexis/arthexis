from __future__ import annotations

import os
from pathlib import PureWindowsPath
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable
from apps.recipes.utils import serialize_recipe_result
from apps.recipes.utils import resolve_arg_sigils


class RecipeManager(models.Manager):
    def get_by_natural_key(self, uuid_value: str):  # pragma: no cover - fixture helper
        return self.get(uuid=uuid_value)


@dataclass(frozen=True)
class RecipeExecutionResult:
    result: Any
    result_variable: str
    resolved_script: str


class RecipeExecutionError(RuntimeError):
    """Raised when recipe execution fails for any supported runtime."""


class RecipeFormatDetectionError(RecipeExecutionError):
    """Raised when recipe content cannot be mapped to an executable runtime."""


class Recipe(Ownable):
    """Executable recipe definition with selectable script runtime."""

    objects = RecipeManager()

    class BodyType(models.TextChoices):
        """Supported runtimes for a recipe body."""

        PYTHON = "python", _("Python")
        BASH = "bash", _("Bash")

    class RecipeFormat(models.TextChoices):
        """Auto-detected recipe formats."""

        MARKDOWN = "markdown", _("Markdown")
        PYTHON = "python", _("Python")
        BASH = "bash", _("Bash")

    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Stable identifier used for natural keys and API references."),
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text=_("Unique slug used to call this recipe from CLI helpers."),
    )
    display = models.CharField(max_length=150, verbose_name=_("Verbose name"))
    script = models.TextField(
        help_text=_(
            "Script contents. [SIGILS] and [ARG.*] tokens are resolved before execution."
        )
    )
    body_type = models.CharField(
        max_length=16,
        choices=BodyType.choices,
        default=BodyType.PYTHON,
        help_text=_("Runtime used to execute this recipe body."),
    )
    result_variable = models.CharField(
        max_length=64,
        default="result",
        help_text=_(
            "Variable name expected to contain the final recipe result."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display",)
        verbose_name = _("Recipe")
        verbose_name_plural = _("Recipes")

    def natural_key(self):  # pragma: no cover - simple representation
        return (str(self.uuid),)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display

    def resolve_script(self, *args: Any, **kwargs: Any) -> str:
        # Local import to avoid circular dependency with the sigils app.
        from apps.sigils.sigil_resolver import resolve_sigils

        resolved = resolve_sigils(self.script or "", current=self)
        return resolve_arg_sigils(resolved, args, kwargs)

    def _resolve_script_without_args(self) -> str:
        """Resolve non-argument sigils while preserving ``[ARG.*]`` tokens."""

        # Local import to avoid circular dependency with the sigils app.
        from apps.sigils.sigil_resolver import resolve_sigils

        return resolve_sigils(self.script or "", current=self)

    def _resolve_bash_script(self, *args: Any, **kwargs: Any) -> str:
        """Resolve bash script content with safely shell-quoted argument sigils."""

        quoted_args = tuple(shlex.quote(str(value)) for value in args)
        quoted_kwargs = {
            key: shlex.quote(str(value)) for key, value in kwargs.items()
        }
        return self.resolve_script(*quoted_args, **quoted_kwargs)

    def execute(self, *args: Any, **kwargs: Any) -> RecipeExecutionResult:
        """Execute a recipe and return the execution metadata and result."""

        result_key = (self.result_variable or "result").strip() or "result"
        recipe_format = self.detect_format()

        if recipe_format == self.RecipeFormat.MARKDOWN:
            resolved_script = self._resolve_script_without_args()
            execution = self._execute_markdown(
                resolved_script=resolved_script,
                result_variable=result_key,
                args=args,
                kwargs=kwargs,
            )
            self._record_product(execution=execution, args=args, kwargs=kwargs)
            return execution

        if recipe_format == self.RecipeFormat.BASH:
            resolved_script = self._resolve_bash_script(*args, **kwargs)
            execution = self._execute_bash(
                resolved_script=resolved_script,
                result_variable=result_key,
                args=args,
                kwargs=kwargs,
            )
            self._record_product(execution=execution, args=args, kwargs=kwargs)
            return execution

        resolved_script = self.resolve_script(*args, **kwargs)
        if recipe_format != self.RecipeFormat.PYTHON:
            raise RecipeExecutionError(
                f"Unsupported recipe format for '{self.slug}': {recipe_format}"
            )

        execution = self._execute_python(
            resolved_script=resolved_script,
            result_variable=result_key,
            args=args,
            kwargs=kwargs,
        )
        self._record_product(execution=execution, args=args, kwargs=kwargs)
        return execution

    def detect_format(self) -> str:
        """Auto-detect the runtime format for the recipe body.

        Markdown is detected from ``.md`` slug suffixes or fenced code blocks.
        Single-language recipes are inferred from slug extensions and finally fall
        back to the legacy ``body_type`` field for backwards compatibility.
        """

        lowered_slug = self.slug.lower()
        script = self.script or ""

        if lowered_slug.endswith(".md") or self._contains_markdown_blocks(script):
            return self.RecipeFormat.MARKDOWN

        extension_map = {
            ".py": self.RecipeFormat.PYTHON,
            ".sh": self.RecipeFormat.BASH,
            ".bash": self.RecipeFormat.BASH,
        }
        for suffix, detected in extension_map.items():
            if lowered_slug.endswith(suffix):
                return detected

        if self.body_type == self.BodyType.PYTHON:
            return self.RecipeFormat.PYTHON
        if self.body_type == self.BodyType.BASH:
            return self.RecipeFormat.BASH

        raise RecipeFormatDetectionError(
            f"Unable to detect recipe format for '{self.slug}' with body type '{self.body_type}'."
        )

    @staticmethod
    def _contains_markdown_blocks(text: str) -> bool:
        """Return True when the provided text contains fenced Markdown code blocks."""

        return bool(re.search(r"```[\w+-]*\n.*?\n```", text, flags=re.DOTALL))

    def _execute_python(
        self,
        *,
        resolved_script: str,
        result_variable: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> RecipeExecutionResult:
        """Execute a Python recipe body in a restricted runtime."""

        safe_builtins = {
            "abs": abs,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "print": print,
            "range": range,
            "repr": repr,
            "set": set,
            "str": str,
            "sum": sum,
            "tuple": tuple,
        }
        exec_globals: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "args": args,
            "kwargs": kwargs,
            "recipe": self,
        }
        exec_locals: dict[str, Any] = {}
        try:
            exec(resolved_script, exec_globals, exec_locals)
        except Exception as exc:
            raise RecipeExecutionError(
                f"Error executing recipe '{self.slug}': {exc}"
            ) from exc

        if result_variable in exec_locals:
            result = exec_locals[result_variable]
        elif result_variable in exec_globals:
            result = exec_globals[result_variable]
        elif result_variable != "result" and "result" in exec_locals:
            result = exec_locals["result"]
        elif result_variable != "result" and "result" in exec_globals:
            result = exec_globals["result"]
        else:
            result = None

        return RecipeExecutionResult(
            result=result,
            result_variable=result_variable,
            resolved_script=resolved_script,
        )

    def _execute_bash(
        self,
        *,
        resolved_script: str,
        result_variable: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> RecipeExecutionResult:
        """Execute a Bash recipe body and return its stdout as the recipe result."""

        def normalize_key(key: str) -> str:
            normalized = re.sub(r"[^A-Za-z0-9]", "_", key).upper()
            if normalized and normalized[0].isdigit():
                normalized = f"_{normalized}"
            return normalized

        allowed_env_keys = {
            "HOME",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "PATH",
            "PWD",
            "TERM",
            "TZ",
            "USER",
        }

        environment = {
            **{
                key: value
                for key, value in os.environ.items()
                if key in allowed_env_keys
            },
            **{
                f"RECIPE_KWARG_{normalize_key(key)}": str(value)
                for key, value in kwargs.items()
            },
        }

        shell_candidates = self._bash_shell_candidates()
        last_error: subprocess.CalledProcessError | OSError | None = None

        for shell in shell_candidates:
            try:
                completed = subprocess.run(
                    [shell, "-c", resolved_script, "recipe", *[str(value) for value in args]],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=environment,
                )
                break
            except subprocess.CalledProcessError as exc:
                last_error = exc
                if not self._is_windows_bash_launcher_failure(exc, shell=shell):
                    stderr = (exc.stderr or "").strip()
                    message = stderr or str(exc)
                    raise RecipeExecutionError(
                        f"Error executing recipe '{self.slug}': {message}"
                    ) from exc
            except OSError as exc:
                last_error = exc
                if not self._is_shell_missing(exc, shell=shell):
                    raise RecipeExecutionError(
                        f"Error executing recipe '{self.slug}': {exc}"
                    ) from exc
        else:
            if isinstance(last_error, subprocess.CalledProcessError):
                stderr = (last_error.stderr or "").strip()
                stdout = (last_error.stdout or "").strip()
                message = stderr or stdout or str(last_error)
            else:
                message = str(last_error) if last_error is not None else "No shell available"
            raise RecipeExecutionError(f"Error executing recipe '{self.slug}': {message}")

        return RecipeExecutionResult(
            result=completed.stdout.rstrip("\n") or None,
            result_variable=result_variable,
            resolved_script=resolved_script,
        )

    def _execute_markdown(
        self,
        *,
        resolved_script: str,
        result_variable: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> RecipeExecutionResult:
        """Execute fenced Markdown code blocks and return rendered Markdown output."""

        code_block_pattern = re.compile(
            r"```(?P<language>[\w+-]*)\n(?P<code>.*?)\n```",
            flags=re.DOTALL,
        )
        parts: list[str] = []
        cursor = 0
        for match in code_block_pattern.finditer(resolved_script):
            prose_segment = resolved_script[cursor:match.start()]
            parts.append(resolve_arg_sigils(prose_segment, args, kwargs))
            language = (match.group("language") or "python").strip().lower()
            block_code = match.group("code")
            block_output = self._execute_language_block(
                language=language,
                code=block_code,
                args=args,
                kwargs=kwargs,
                result_variable=result_variable,
            )
            parts.append(serialize_recipe_result(block_output))
            cursor = match.end()
        tail_segment = resolved_script[cursor:]
        parts.append(resolve_arg_sigils(tail_segment, args, kwargs))
        rendered_markdown = "".join(parts)

        return RecipeExecutionResult(
            result=rendered_markdown,
            result_variable=result_variable,
            resolved_script=resolved_script,
        )

    def _execute_language_block(
        self,
        *,
        language: str,
        code: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result_variable: str,
    ) -> Any:
        """Execute a language block and return its output.

        Python and bash are executed with the same runtime semantics used by
        standalone recipes. Other languages are run through subprocess using the
        language token as command name.
        """

        if language in {"", "python", "py"}:
            resolved_code = resolve_arg_sigils(code, args, kwargs)
            execution = self._execute_python(
                resolved_script=resolved_code,
                result_variable=result_variable,
                args=args,
                kwargs=kwargs,
            )
            return execution.result

        if language in {"bash", "sh", "shell"}:
            quoted_args = tuple(shlex.quote(str(value)) for value in args)
            quoted_kwargs = {
                key: shlex.quote(str(value)) for key, value in kwargs.items()
            }
            resolved_code = resolve_arg_sigils(code, quoted_args, quoted_kwargs)
            execution = self._execute_bash(
                resolved_script=resolved_code,
                result_variable=result_variable,
                args=args,
                kwargs=kwargs,
            )
            return execution.result

        return self._execute_external_language(
            language=language,
            code=code,
            args=args,
            kwargs=kwargs,
        )

    def _execute_external_language(
        self,
        *,
        language: str,
        code: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        """Execute an external interpreter command and return captured stdout."""

        commands_by_language = {
            "javascript": ["node", "-e", code],
            "js": ["node", "-e", code],
            "lua": ["lua", "-e", code],
            "luajit": ["luajit", "-e", code],
            "ruby": ["ruby", "-e", code],
            "rb": ["ruby", "-e", code],
            "perl": ["perl", "-e", code],
            "pwsh": ["pwsh", "-Command", code],
            "powershell": ["powershell", "-Command", code],
        }
        command = commands_by_language.get(language, [language, "-c", code])
        execution_env = os.environ.copy()
        execution_env["RECIPE_ARGS_COUNT"] = str(len(args))
        for index, value in enumerate(args):
            execution_env[f"RECIPE_ARG_{index}"] = str(value)
        for key, value in kwargs.items():
            env_key = re.sub(r"[^A-Za-z0-9_]", "_", key).upper()
            execution_env[f"RECIPE_KW_{env_key}"] = str(value)

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                env=execution_env,
            )
        except FileNotFoundError as exc:
            raise RecipeExecutionError(
                f"Error executing recipe '{self.slug}': interpreter '{language}' is not available"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            message = stderr or str(exc)
            raise RecipeExecutionError(
                f"Error executing recipe '{self.slug}': {message}"
            ) from exc

        return completed.stdout.rstrip("\n")

    def _record_product(
        self,
        *,
        execution: RecipeExecutionResult,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """Persist execution outputs and inputs as a RecipeProduct record."""

        RecipeProduct.objects.create(
            recipe=self,
            format_detected=self.detect_format(),
            input_args=[str(value) for value in args],
            input_kwargs={key: str(value) for key, value in kwargs.items()},
            result=serialize_recipe_result(execution.result),
            result_variable=execution.result_variable,
            resolved_script=execution.resolved_script,
        )

    @staticmethod
    def _shell_basename(shell: str) -> str:
        """Return a shell executable name for both POSIX and Windows path strings."""

        normalized = PureWindowsPath(shell).name.strip().lower()
        return normalized or shell.strip().lower()

    @staticmethod
    def _is_windows_bash_launcher_failure(
        exc: subprocess.CalledProcessError, *, shell: str
    ) -> bool:
        """Return True when Windows bash launcher fails before script execution.

        Launcher diagnostics may be emitted in NUL-delimited chunks, so output
        is normalized before checking known failure signatures.

        The caller treats this as a bootstrap problem (not a script failure) and
        continues to the next shell candidate if one exists.
        """

        if os.name != "nt" or Recipe._shell_basename(shell) not in {"bash", "bash.exe"}:
            return False

        messages: list[str] = []
        compact_messages: list[str] = []
        for stream in (exc.stdout, exc.stderr, str(exc)):
            raw_text = stream or ""
            text = re.sub(r"\s+", " ", raw_text.replace("\x00", " ")).strip()
            if text:
                messages.append(text)

            compact_text = re.sub(r"[\s\x00]+", "", raw_text)
            if compact_text:
                compact_messages.append(compact_text)

        output = "\n".join(messages).lower()
        compact_output = "".join(compact_messages).lower()
        signatures = (
            ("wsl/service", "wsl/service"),
            ("wsl", "wsl"),
            ("rpc call", "rpccall"),
        )
        return any(
            signature in output or compact_signature in compact_output
            for signature, compact_signature in signatures
        )

    @staticmethod
    def _is_shell_missing(exc: OSError, *, shell: str) -> bool:
        """Return True when a shell candidate is unavailable on this host."""

        if not isinstance(exc, FileNotFoundError):
            return False
        shell_name = Recipe._shell_basename(shell)
        return shell_name in {"bash", "bash.exe", "sh", "sh.exe"}

    @staticmethod
    def _bash_shell_candidates() -> tuple[str, ...]:
        """Return shell candidates for bash recipes in priority order.

        Regression: on Windows, plain ``bash`` can resolve to the WSL launcher,
        which fails before running scripts. Prefer local POSIX shell binaries from
        common Git/MSYS installations as additional fallbacks.
        """

        candidates: list[str] = ["bash", "sh"]

        if os.name == "nt":
            program_files = os.environ.get("PROGRAMFILES", "C:/Program Files")
            program_files_path = PureWindowsPath(program_files)
            candidates.extend(
                [
                    str(program_files_path / "Git" / "bin" / "bash.exe"),
                    str(program_files_path / "Git" / "usr" / "bin" / "sh.exe"),
                    "C:/msys64/usr/bin/bash.exe",
                    "C:/msys64/usr/bin/sh.exe",
                ]
            )

        return tuple(dict.fromkeys(candidates))


class RecipeProduct(models.Model):
    """Persistent execution artifact generated each time a recipe runs."""

    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="products",
    )
    format_detected = models.CharField(max_length=32)
    input_args = models.JSONField(default=list)
    input_kwargs = models.JSONField(default=dict)
    result = models.TextField(blank=True)
    result_variable = models.CharField(max_length=64)
    resolved_script = models.TextField()
    executed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-executed_at",)
        verbose_name = _("Recipe Product")
        verbose_name_plural = _("Recipe Products")


__all__ = [
    "Recipe",
    "RecipeExecutionError",
    "RecipeExecutionResult",
    "RecipeFormatDetectionError",
    "RecipeProduct",
]
