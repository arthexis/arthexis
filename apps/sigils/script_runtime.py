"""Sigil script parsing and execution helpers for CLI and integration workflows."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from .models import SigilRoot
from .sigil_resolver import (
    get_user_safe_sigil_actions,
    get_user_safe_sigil_roots,
    resolve_sigils,
)


class ScriptParseError(ValueError):
    """Raised when a sigil script line cannot be parsed."""


class ScriptPolicyError(ValueError):
    """Raised when resolver policy blocks a script expression."""


class ScriptRuntimeError(ValueError):
    """Raised when execution fails after parsing and policy checks."""


@dataclass(frozen=True)
class ScriptInstruction:
    """One parsed script instruction."""

    action: str
    argument: str
    line_number: int
    identifier: str | None = None


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNRESOLVED_SIGIL_RE = re.compile(r"\[[A-Za-z0-9_-]+(?:[:=.]|\||->)")


def parse_script(text: str) -> list[ScriptInstruction]:
    """Parse deterministic LET/EMIT sigil script statements."""

    instructions: list[ScriptInstruction] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.upper().startswith("LET "):
            statement = line[4:].strip()
            if "=" not in statement:
                raise ScriptParseError(
                    f"line {line_number}: LET requires `NAME = EXPR` syntax"
                )
            identifier_raw, expression = statement.split("=", 1)
            identifier = identifier_raw.strip().upper()
            expression = expression.strip()
            if not identifier or not _IDENTIFIER_RE.match(identifier):
                raise ScriptParseError(
                    f"line {line_number}: invalid LET identifier `{identifier_raw.strip()}`"
                )
            if not expression:
                raise ScriptParseError(f"line {line_number}: LET expression cannot be empty")
            instructions.append(
                ScriptInstruction(
                    action="LET",
                    argument=expression,
                    identifier=identifier,
                    line_number=line_number,
                )
            )
            continue

        if line.upper().startswith("EMIT "):
            expression = line[5:].strip()
            if not expression:
                raise ScriptParseError(f"line {line_number}: EMIT expression cannot be empty")
            instructions.append(
                ScriptInstruction(
                    action="EMIT",
                    argument=expression,
                    line_number=line_number,
                )
            )
            continue

        action = line.split(" ", 1)[0].upper()
        raise ScriptParseError(f"line {line_number}: unsupported action `{action}`")

    return instructions


def _resolve_policy(context: str) -> tuple[set[str] | None, set[str] | None]:
    context_name = context.lower()
    if context_name == "admin":
        return None, None
    if context_name == "user":
        return get_user_safe_sigil_roots(), get_user_safe_sigil_actions()
    if context_name == "request":
        roots = {
            root.upper()
            for root in SigilRoot.objects.filter(
                context_type=SigilRoot.Context.REQUEST
            ).values_list("prefix", flat=True)
        }
        return roots, None
    raise ScriptPolicyError(f"unsupported context `{context}`")


def _interpolate_variables(expression: str, variables: dict[str, str]) -> str:
    if not variables:
        return expression

    sorted_keys = sorted(variables, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(f"${key}") for key in sorted_keys))
    return pattern.sub(lambda match: variables[match.group()[1:]], expression)


def execute_script(
    instructions: list[ScriptInstruction],
    *,
    context: str,
) -> list[str]:
    """Execute parsed script instructions and return EMIT outputs."""

    allowed_roots, allowed_actions = _resolve_policy(context)
    variables: dict[str, str] = {}
    outputs: list[str] = []

    for instruction in instructions:
        source_expression = _interpolate_variables(instruction.argument, variables)
        resolved = resolve_sigils(
            source_expression,
            allowed_roots=allowed_roots,
            allowed_actions=allowed_actions,
        )
        if _UNRESOLVED_SIGIL_RE.search(resolved):
            raise ScriptPolicyError(
                f"line {instruction.line_number}: expression blocked or unresolved"
            )
        if instruction.action == "LET":
            if instruction.identifier is None:
                raise ScriptRuntimeError(
                    f"line {instruction.line_number}: LET identifier missing"
                )
            variables[instruction.identifier] = resolved
        elif instruction.action == "EMIT":
            outputs.append(resolved)
        else:
            raise ScriptRuntimeError(
                f"line {instruction.line_number}: unknown action `{instruction.action}`"
            )

    return outputs


@lru_cache(maxsize=512)
def _execute_script_text_cached(script_text: str, context: str) -> tuple[str, ...]:
    return tuple(execute_script(parse_script(script_text), context=context))


def clear_script_execution_cache() -> None:
    """Clear cached script execution outputs used by resolve-like commands."""

    _execute_script_text_cached.cache_clear()


def execute_script_text(
    script_text: str,
    *,
    context: str = "admin",
    use_cache: bool = False,
) -> list[str]:
    """Parse and execute script text in one step."""

    if use_cache:
        return list(_execute_script_text_cached(script_text, context))
    return execute_script(parse_script(script_text), context=context)
