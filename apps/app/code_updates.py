from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.apps import apps as django_apps


def get_application_code_cache_path() -> Path:
    return (
        Path(__file__).resolve().parent
        / "static"
        / "app"
        / "application_code_updates.json"
    )


def _normalize_cache_payload(payload: dict[str, Any]) -> dict[str, str]:
    applications = payload.get("applications", payload)
    if not isinstance(applications, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in applications.items()
        if isinstance(key, str) and isinstance(value, str)
    }


@lru_cache(maxsize=1)
def _load_application_code_cache() -> dict[str, str]:
    cache_path = get_application_code_cache_path()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return _normalize_cache_payload(payload)
    return {}


def _parse_cache_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def get_application_code_update_date(app_name: str) -> date | None:
    if not app_name:
        return None
    try:
        config = django_apps.get_app_config(app_name)
    except LookupError:
        config = next(
            (
                candidate
                for candidate in django_apps.get_app_configs()
                if candidate.name == app_name
            ),
            None,
        )
    cache_key = config.label if config else app_name
    cache = _load_application_code_cache()
    return _parse_cache_date(cache.get(cache_key, ""))
