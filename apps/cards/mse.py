from __future__ import annotations

from io import BytesIO
import re
from typing import Any
from zipfile import BadZipFile, ZipFile


_NORMALIZE_RE = re.compile(r"[^0-9a-z]+")


def _normalize_key(key: str) -> str:
    return _NORMALIZE_RE.sub("_", key.lower()).strip("_")


def _find_key(mapping: dict[str, Any], *keys: str) -> str | None:
    normalized = {_normalize_key(key): key for key in mapping}
    for key in keys:
        match = normalized.get(_normalize_key(key))
        if match:
            return match
    return None


def _lookup(mapping: dict[str, Any], *keys: str) -> Any:
    match = _find_key(mapping, *keys)
    if match is None:
        return None
    return mapping.get(match)


def _lookup_scalar(mapping: dict[str, Any], *keys: str) -> str:
    value = _lookup(mapping, *keys)
    if isinstance(value, (dict, list)):
        return ""
    if value is None:
        return ""
    return str(value)


def extract_set_text(payload: bytes) -> str:
    """Return the set text from a raw MSE payload (zip or plaintext)."""

    try:
        with ZipFile(BytesIO(payload)) as archive:
            set_name = next(
                (name for name in archive.namelist() if name == "set" or name.endswith("/set")),
                None,
            )
            if not set_name:
                raise KeyError("set")
            raw = archive.read(set_name)
            return raw.decode("utf-8", errors="replace")
    except (BadZipFile, KeyError):
        return payload.decode("utf-8", errors="replace")


def parse_mse_set(text: str) -> dict[str, Any]:
    """Parse MSE set text into a nested mapping."""

    root: dict[str, Any] = {}
    stack: list[dict[str, Any]] = [{"indent": -1, "container": root, "last_key": None}]

    for raw_line in text.splitlines():
        line = raw_line.expandtabs(4).rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)

        def pop_to_indent():
            while stack and indent <= stack[-1]["indent"]:
                stack.pop()

        pop_to_indent()

        if ":" not in stripped:
            current = stack[-1]
            last_key = current.get("last_key")
            if not last_key:
                continue
            container = current["container"]
            existing = container.get(last_key)
            if isinstance(existing, list):
                if not existing:
                    continue
                target = existing[-1]
                if isinstance(target, str):
                    existing[-1] = f"{target}\n{stripped}"
            elif isinstance(existing, str):
                container[last_key] = f"{existing}\n{stripped}"
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.lstrip()
        parent = stack[-1]["container"]

        def append_value(container: dict[str, Any], key_name: str, item: Any) -> Any:
            current = container.get(key_name)
            if current is None:
                container[key_name] = item
                return item
            if isinstance(current, list):
                current.append(item)
                return item
            container[key_name] = [current, item]
            return item

        if value == "":
            child: dict[str, Any] = {}
            append_value(parent, key, child)
            stack.append({"indent": indent, "container": child, "last_key": None})
        else:
            append_value(parent, key, value)
            stack[-1]["last_key"] = key

    return root


def extract_set_metadata(parsed: dict[str, Any]) -> dict[str, Any]:
    """Extract common metadata sections from a parsed MSE set."""

    set_info = _lookup(parsed, "set info", "set_info", "setinfo")
    style_settings = _lookup(parsed, "style settings", "style_settings")
    style_value = _lookup(parsed, "style")

    if isinstance(style_value, dict) and not isinstance(style_settings, dict):
        style_settings = style_value
        style_value = ""

    set_info = set_info if isinstance(set_info, dict) else {}
    style_settings = style_settings if isinstance(style_settings, dict) else {}

    return {
        "game": _lookup_scalar(parsed, "game"),
        "style": _lookup_scalar(parsed, "style") if not isinstance(style_value, dict) else "",
        "set_info": set_info,
        "style_settings": style_settings,
    }


def extract_set_name(parsed: dict[str, Any], *, default: str = "") -> str:
    set_info = _lookup(parsed, "set info", "set_info", "setinfo")
    info = set_info if isinstance(set_info, dict) else {}
    return _lookup_scalar(info, "title", "name", "set name", "set_name") or default


def extract_set_code(parsed: dict[str, Any]) -> str:
    set_info = _lookup(parsed, "set info", "set_info", "setinfo")
    info = set_info if isinstance(set_info, dict) else {}
    return _lookup_scalar(info, "set code", "set_code", "code", "short name", "short_name")


def extract_set_language(parsed: dict[str, Any]) -> str:
    set_info = _lookup(parsed, "set info", "set_info", "setinfo")
    info = set_info if isinstance(set_info, dict) else {}
    return _lookup_scalar(info, "language")


def extract_cards(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    cards = _lookup(parsed, "card")
    if isinstance(cards, list):
        return [card for card in cards if isinstance(card, dict)]
    if isinstance(cards, dict):
        return [cards]
    return []


def extract_card_name(card: dict[str, Any], *, default: str = "") -> str:
    return _lookup_scalar(card, "name", "title", "card name", "card_name") or default
