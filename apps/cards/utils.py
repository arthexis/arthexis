from __future__ import annotations

from typing import Tuple

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.cards.models import RFID


def normalize_endianness(value) -> str:
    """Normalize a raw endianness value to one of the RFID choices."""

    if isinstance(value, str):
        candidate = value.strip().upper()
        valid = {choice[0] for choice in RFID.ENDIANNESS_CHOICES}
        if candidate in valid:
            return candidate
    return RFID.BIG_ENDIAN


def convert_endianness_value(
    value: str,
    *,
    from_endianness: str | None = None,
    to_endianness: str | None = None,
) -> str:
    """Convert ``value`` between big and little endian representations."""

    if not isinstance(value, str):
        return ""
    sanitized = "".join(value.split()).upper()
    if not sanitized:
        return ""
    source = normalize_endianness(from_endianness or RFID.BIG_ENDIAN)
    target = normalize_endianness(to_endianness or RFID.BIG_ENDIAN)
    if source == target:
        return sanitized
    if len(sanitized) % 2 != 0:
        return sanitized
    bytes_list = [sanitized[i : i + 2] for i in range(0, len(sanitized), 2)]
    bytes_list.reverse()
    return "".join(bytes_list)


def build_mode_toggle(
    request: HttpRequest, *, base_path: str | None = None
) -> Tuple[bool, str, str]:
    """Return table mode flag and toggle details for the RFID views."""

    params = request.GET.copy()
    mode = params.get("mode")
    table_mode = mode == "table"

    params = params.copy()
    params._mutable = True
    if table_mode:
        params.pop("mode", None)
        toggle_label = _("Single Mode")
    else:
        params["mode"] = "table"
        toggle_label = _("Table Mode")

    toggle_url = base_path or request.path
    toggle_query = params.urlencode()
    if toggle_query:
        toggle_url = f"{toggle_url}?{toggle_query}"

    return table_mode, toggle_url, toggle_label
