from __future__ import annotations

from typing import Tuple

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _


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
