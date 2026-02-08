"""Shared helper utilities for call handler modules."""
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


def _extract_component_variable(entry: dict) -> tuple[str, str, str, str]:
    component_data = entry.get("component")
    variable_data = entry.get("variable")
    if not isinstance(component_data, dict) or not isinstance(variable_data, dict):
        return "", "", "", ""
    component_name = str(component_data.get("name") or "").strip()
    component_instance = str(component_data.get("instance") or "").strip()
    variable_name = str(variable_data.get("name") or "").strip()
    variable_instance = str(variable_data.get("instance") or "").strip()
    return component_name, component_instance, variable_name, variable_instance


def _json_details(details: dict | None) -> str:
    if not details:
        return ""
    try:
        return json.dumps(details, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(details)
