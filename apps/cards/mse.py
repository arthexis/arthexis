"""Utilities for parsing Magic Set Editor 2 set files."""
from __future__ import annotations

from dataclasses import dataclass
import zipfile

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


@dataclass
class ParsedMSESet:
    raw_text: str
    game: str
    style: str
    title: str
    code: str
    language: str
    set_info: dict
    style_settings: dict
    cards: list[dict]


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _store_value(container: dict, key: str, value):
    if key in container:
        existing = container[key]
        if isinstance(existing, list):
            existing.append(value)
        else:
            container[key] = [existing, value]
    else:
        container[key] = value


def _parse_block(lines: list[str], start: int, base_indent: int) -> tuple[dict, int]:
    container: dict = {}
    last_key: str | None = None
    index = start
    while index < len(lines):
        raw_line = lines[index]
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            index += 1
            continue
        indent = _indent_of(raw_line)
        if indent < base_indent:
            break
        stripped = raw_line.lstrip(" \t")
        if ":" in stripped:
            key, remainder = stripped.split(":", 1)
            key = key.strip()
            value = remainder.lstrip(" ").rstrip("\n")
            if value == "":
                nested, index = _parse_block(lines, index + 1, indent + 1)
                value = nested
            else:
                index += 1
            _store_value(container, key, value)
            last_key = key
        else:
            if last_key:
                previous = container[last_key]
                appended = stripped.strip()
                if isinstance(previous, list):
                    previous[-1] = f"{previous[-1]}\n{appended}"
                else:
                    container[last_key] = f"{previous}\n{appended}"
            index += 1
    return container, index


def parse_mse_text(raw_text: str) -> dict:
    lines = raw_text.splitlines()
    parsed, _ = _parse_block(lines, 0, 0)
    return parsed


def _read_set_text(upload) -> str:
    upload.seek(0)
    if zipfile.is_zipfile(upload):
        upload.seek(0)
        with zipfile.ZipFile(upload) as archive:
            set_candidates = [
                name for name in archive.namelist() if name == "set" or name.endswith("/set")
            ]
            if not set_candidates:
                raise ValidationError(_("Uploaded MSE set did not include a set file."))
            with archive.open(set_candidates[0]) as set_file:
                raw_bytes = set_file.read()
    else:
        raw_bytes = upload.read()
    return raw_bytes.decode("utf-8", errors="replace")


def _get_section(parsed: dict, *names: str) -> dict:
    for name in names:
        section = parsed.get(name)
        if isinstance(section, dict):
            return section
    return {}


def _get_value(mapping: dict, *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_mse_set(upload) -> ParsedMSESet:
    raw_text = _read_set_text(upload)
    parsed = parse_mse_text(raw_text)
    set_info = _get_section(parsed, "set info", "set_info", "set-info")
    style_settings = _get_section(parsed, "style settings", "style_settings", "style-settings")

    game = _get_value(parsed, "game", "game id", "game_id")
    style = _get_value(parsed, "style", "style id", "style_id")

    title = _get_value(set_info, "title", "name")
    code = _get_value(set_info, "set code", "code", "set_code")
    language = _get_value(set_info, "language", "lang")

    cards_section = parsed.get("card", [])
    if isinstance(cards_section, dict):
        cards = [cards_section]
    elif isinstance(cards_section, list):
        cards = [item for item in cards_section if isinstance(item, dict)]
    else:
        cards = []

    return ParsedMSESet(
        raw_text=raw_text,
        game=game,
        style=style,
        title=title,
        code=code,
        language=language,
        set_info=set_info,
        style_settings=style_settings,
        cards=cards,
    )
