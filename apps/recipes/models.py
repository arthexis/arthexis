from __future__ import annotations

import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable
from apps.recipes.utils import resolve_arg_sigils


class RecipeManager(models.Manager):
    def get_by_natural_key(self, uuid_value: str):  # pragma: no cover - fixture helper
        return self.get(uuid=uuid_value)


@dataclass(frozen=True)
class RecipeExecutionResult:
    result: Any
    result_variable: str
    resolved_script: str


class Recipe(Ownable):
    """Executable recipe definition with selectable script runtime."""

    objects = RecipeManager()

    class BodyType(models.TextChoices):
        """Supported runtimes for a recipe body."""

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
        resolved = resolve_arg_sigils(self.script or "", args, kwargs)
        # Local import to avoid circular dependency with the sigils app.
        from apps.sigils.sigil_resolver import resolve_sigils

        return resolve_sigils(resolved, current=self)

    def execute(self, *args: Any, **kwargs: Any) -> RecipeExecutionResult:
        """Execute a recipe and return the execution metadata and result."""

        resolved_script = self.resolve_script(*args, **kwargs)
        result_key = (self.result_variable or "result").strip() or "result"

        if self.body_type == self.BodyType.BASH:
            return self._execute_bash(
                resolved_script=resolved_script,
                result_variable=result_key,
                args=args,
                kwargs=kwargs,
            )

        if self.body_type != self.BodyType.PYTHON:
            raise RuntimeError(
                f"Unsupported recipe body type for '{self.slug}': {self.body_type}"
            )

        return self._execute_python(
            resolved_script=resolved_script,
            result_variable=result_key,
            args=args,
            kwargs=kwargs,
        )

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
            raise RuntimeError(
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

        shell_candidates = ("bash", "sh")
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
                    raise RuntimeError(
                        f"Error executing recipe '{self.slug}': {message}"
                    ) from exc
            except OSError as exc:
                last_error = exc
                if not self._is_shell_missing(exc, shell=shell):
                    raise RuntimeError(
                        f"Error executing recipe '{self.slug}': {exc}"
                    ) from exc
        else:
            if isinstance(last_error, subprocess.CalledProcessError):
                stderr = (last_error.stderr or "").strip()
                stdout = (last_error.stdout or "").strip()
                message = stderr or stdout or str(last_error)
            else:
                message = str(last_error) if last_error is not None else "No shell available"
            raise RuntimeError(f"Error executing recipe '{self.slug}': {message}")

        return RecipeExecutionResult(
            result=completed.stdout.rstrip("\n") or None,
            result_variable=result_variable,
            resolved_script=resolved_script,
        )

    @staticmethod
    def _is_windows_bash_launcher_failure(
        exc: subprocess.CalledProcessError, *, shell: str
    ) -> bool:
        """Return True when Windows bash launcher fails before script execution.

        The caller treats this as a bootstrap problem (not a script failure) and
        continues to the next shell candidate if one exists.
        """

        if os.name != "nt" or shell != "bash":
            return False
        output = f"{exc.stdout or ''}\n{exc.stderr or ''}".lower()
        return "wsl" in output or "rpc call" in output

    @staticmethod
    def _is_shell_missing(exc: OSError, *, shell: str) -> bool:
        """Return True when a shell candidate is unavailable on this host."""

        return shell in {"bash", "sh"} and isinstance(exc, FileNotFoundError)


__all__ = ["Recipe", "RecipeExecutionResult"]
