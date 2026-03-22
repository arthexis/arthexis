"""Utility helpers for structured shortcut execution and templates."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

_ARG_TOKEN = re.compile(r"\[ARG\.([^\]]+)\]")


def resolve_arg_tokens(text: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
    """Resolve ``[ARG.*]`` placeholders against positional and keyword values.

    Parameters:
        text: Template text that may contain ``[ARG.<index>]`` or ``[ARG.<name>]`` tokens.
        args: Positional values available to numeric placeholders.
        kwargs: Keyword values available to named placeholders.

    Returns:
        str: The text with recognized placeholders replaced by stringified values.
    """

    if not text:
        return text

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value: Any = None
        if key.isdigit():
            index = int(key)
            if 0 <= index < len(args):
                value = args[index]
        else:
            value = kwargs.get(key)
        return "" if value is None else str(value)

    return _ARG_TOKEN.sub(replace, text)


__all__ = ["resolve_arg_tokens"]
