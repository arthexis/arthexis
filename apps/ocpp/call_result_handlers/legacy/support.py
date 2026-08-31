from __future__ import annotations

import json


def _format_status_info(status_info: object) -> str:
    if not status_info:
        return ""
    if isinstance(status_info, str):
        return status_info.strip()
    try:
        return json.dumps(status_info, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(status_info)
