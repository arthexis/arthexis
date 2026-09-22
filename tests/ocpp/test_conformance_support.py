from tests.apps.ocpp.conformance_support import minimal_instance


def test_minimal_instance_builds_required_nested_values() -> None:
    schema = {
        "type": "object",
        "required": ["timestamp", "status", "nested"],
        "properties": {
            "timestamp": {"type": "string", "format": "date-time"},
            "status": {"type": "string", "enum": ["Accepted", "Rejected"]},
            "nested": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "integer", "minimum": 1}},
            },
        },
    }

    assert minimal_instance(schema) == {
        "timestamp": "2020-01-01T00:00:00Z",
        "status": "Accepted",
        "nested": {"value": 1},
    }


def test_minimal_instance_resolves_local_refs() -> None:
    schema = {
        "type": "object",
        "required": ["token"],
        "properties": {"token": {"$ref": "#/$defs/Token"}},
        "$defs": {
            "Token": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "minLength": 2}},
            }
        },
    }

    assert minimal_instance(schema) == {"token": {"id": "xx"}}
