from __future__ import annotations

import json

from django.apps import apps
from django.db.models import Model


def _model_has_seed_field(model: type[Model]) -> bool:
    return any(field.name == "is_seed_data" for field in model._meta.fields)


def ensure_seed_data_flags(content: str) -> str:
    """Ensure serialized fixtures mark seedable models as seed data.

    The function parses a Django JSON fixture string and sets
    ``fields["is_seed_data"]`` to ``True`` for any entries whose models
    declare the ``is_seed_data`` field. If parsing fails or no changes are
    necessary, the original content is returned unchanged. A trailing newline
    is always ensured when returning serialized content.
    """

    try:
        payload = json.loads(content)
    except Exception:
        return content if content.endswith("\n") else content + "\n"

    if not isinstance(payload, list):
        return content if content.endswith("\n") else content + "\n"

    changed = False
    for record in payload:
        if not isinstance(record, dict):
            continue
        model_label = record.get("model")
        fields = record.get("fields")
        if not model_label or not isinstance(fields, dict):
            continue
        try:
            model = apps.get_model(model_label)
        except LookupError:
            continue
        if _model_has_seed_field(model) and fields.get("is_seed_data") is not True:
            fields["is_seed_data"] = True
            changed = True

    if changed:
        content = json.dumps(payload, indent=2)

    if not content.endswith("\n"):
        content += "\n"

    return content
