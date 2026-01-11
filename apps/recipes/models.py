from __future__ import annotations

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
    objects = RecipeManager()

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
            "Python script contents. [SIGILS] and [ARG.*] tokens are resolved before execution."
        )
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
        resolved_script = self.resolve_script(*args, **kwargs)
        result_key = (self.result_variable or "result").strip() or "result"

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

        if result_key in exec_locals:
            result = exec_locals[result_key]
        elif result_key in exec_globals:
            result = exec_globals[result_key]
        elif result_key != "result" and "result" in exec_locals:
            result = exec_locals["result"]
        elif result_key != "result" and "result" in exec_globals:
            result = exec_globals["result"]
        else:
            result = None

        return RecipeExecutionResult(
            result=result,
            result_variable=result_key,
            resolved_script=resolved_script,
        )


__all__ = ["Recipe", "RecipeExecutionResult"]
