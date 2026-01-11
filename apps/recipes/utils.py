from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

_ARG_TOKEN = re.compile(r"\[ARG\.([^\]]+)\]")


def resolve_arg_sigils(text: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
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


def parse_recipe_arguments(values: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    args: list[str] = []
    kwargs: dict[str, str] = {}

    for item in values:
        if "=" in item:
            key, _, value = item.partition("=")
            normalized_key = key.lstrip("-")
            if normalized_key:
                kwargs[normalized_key] = value
                continue
        args.append(item)

    return args, kwargs


def serialize_recipe_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


__all__ = ["parse_recipe_arguments", "resolve_arg_sigils", "serialize_recipe_result"]
