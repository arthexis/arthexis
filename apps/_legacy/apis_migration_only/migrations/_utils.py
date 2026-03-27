"""Shared helpers for legacy API migration fixture loading and model compatibility."""

import json
from pathlib import Path


EVERGO_FIXTURE_FILENAME = "apis__evergo_endpoints.json"
RESOURCE_METHOD_FIXTURE_MODEL = "apis.resourcemethod"
RESOURCE_METHOD_API_FIELD_CANDIDATES = ("api", "api_explorer")


def load_fixture_payload() -> tuple[dict, ...]:
    """Load legacy API endpoint definitions from packaged fixture data."""

    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / EVERGO_FIXTURE_FILENAME
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return tuple(
        entry["fields"]
        for entry in payload
        if entry.get("model") == RESOURCE_METHOD_FIXTURE_MODEL
    )


def resolve_resource_method_api_field_name(ResourceMethod) -> str:
    """Return the API foreign-key name used by the historical ResourceMethod model."""

    field_names = {field.name for field in ResourceMethod._meta.get_fields()}
    for candidate in RESOURCE_METHOD_API_FIELD_CANDIDATES:
        if candidate in field_names:
            return candidate

    model_name = ResourceMethod._meta.object_name
    expected = ", ".join(RESOURCE_METHOD_API_FIELD_CANDIDATES)
    raise LookupError(
        f"{model_name} has no expected API foreign-key field. Expected one of: {expected}."
    )
