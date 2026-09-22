"""Helpers for deterministic official OCPP JSON-schema conformance tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local schema refs are supported: {ref}")
    current: Any = root
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        current = current[key]
    if not isinstance(current, dict):
        raise ValueError(f"Schema ref does not resolve to an object: {ref}")
    return current


def minimal_instance(schema: dict[str, Any], *, root: dict[str, Any] | None = None) -> Any:
    """Build a deterministic minimal instance for the OCPP schema subset."""
    root = root or schema

    if "$ref" in schema:
        return minimal_instance(resolve_ref(root, schema["$ref"]), root=root)

    if "const" in schema:
        return schema["const"]

    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    if "allOf" in schema:
        parts = [minimal_instance(part, root=root) for part in schema["allOf"]]
        if all(isinstance(part, dict) for part in parts):
            merged: dict[str, Any] = {}
            for part in parts:
                merged.update(part)
            return merged
        return parts[-1]

    for union_name in ("oneOf", "anyOf"):
        union = schema.get(union_name)
        if isinstance(union, list) and union:
            return minimal_instance(union[0], root=root)

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")

    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return {
            name: minimal_instance(properties[name], root=root)
            for name in required
            if name in properties
        }

    if schema_type == "array":
        minimum = int(schema.get("minItems", 0))
        count = max(minimum, 1)
        item_schema = schema.get("items", {})
        return [minimal_instance(item_schema, root=root) for _ in range(count)]

    if schema_type == "integer":
        minimum = schema.get("minimum", 0)
        if "exclusiveMinimum" in schema and isinstance(schema["exclusiveMinimum"], (int, float)):
            minimum = max(minimum, schema["exclusiveMinimum"] + 1)
        return int(minimum)

    if schema_type == "number":
        minimum = schema.get("minimum", 0)
        if "exclusiveMinimum" in schema and isinstance(schema["exclusiveMinimum"], (int, float)):
            minimum = max(minimum, schema["exclusiveMinimum"] + 1)
        return float(minimum)

    if schema_type == "boolean":
        return False

    if schema_type == "null":
        return None

    if schema_type == "string" or schema_type is None:
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2020-01-01T00:00:00Z"
        if fmt in {"uri", "uri-reference"}:
            return "https://example.com/"
        length = max(int(schema.get("minLength", 1)), 1)
        return "x" * length

    raise ValueError(f"Unsupported schema shape: {schema!r}")
