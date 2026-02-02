from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings


_SEED_FIXTURE_IGNORED_FIELDS = {"is_seed_data", "is_deleted", "is_user_data"}


@lru_cache(maxsize=1)
def _seed_fixture_index():
    base = Path(settings.BASE_DIR)
    index: dict[str, dict[str, object]] = {}
    for path in base.glob("**/fixtures/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        obj = data[0]
        if not isinstance(obj, dict):
            continue
        label = obj.get("model")
        if not isinstance(label, str):
            continue
        fields = obj.get("fields") or {}
        if not isinstance(fields, dict):
            fields = {}
        comparable_fields = {
            key: value
            for key, value in fields.items()
            if key not in _SEED_FIXTURE_IGNORED_FIELDS
        }
        pk = obj.get("pk")
        entries = index.setdefault(label, {"pk": {}, "fields": []})
        pk_index = entries.setdefault("pk", {})
        field_index = entries.setdefault("fields", [])
        if pk is not None:
            pk_index[pk] = path
        if comparable_fields:
            field_index.append((comparable_fields, path))
    return index


def _seed_fixture_path(instance, *, index=None) -> Path | None:
    label = f"{instance._meta.app_label}.{instance._meta.model_name}"
    fixture_index = index or _seed_fixture_index()
    entries = fixture_index.get(label)
    if not entries:
        return None
    pk = getattr(instance, "pk", None)
    pk_index = entries.get("pk", {})
    if pk is not None:
        path = pk_index.get(pk)
        if path is not None:
            return path
    for comparable_fields, path in entries.get("fields", []):
        match = True
        if not isinstance(comparable_fields, dict):
            continue
        for field_name, value in comparable_fields.items():
            if not hasattr(instance, field_name):
                match = False
                break
            if getattr(instance, field_name) != value:
                match = False
                break
        if match:
            return path
    return None
