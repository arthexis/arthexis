from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_SIMPLE_CONDITION_PATTERN = re.compile(
    r"^\s*(?P<left>.+?)\s*(?P<operator>=|!=|<>|>=|<=|>|<)\s*(?P<right>.+?)\s*$"
)
_BOOLEAN_LITERAL_MAP = {
    "0": False,
    "1": True,
    "false": False,
    "no": False,
    "off": False,
    "on": True,
    "true": True,
    "yes": True,
}


@dataclass(frozen=True)
class StructuredCondition:
    source: str
    operator: str
    expected_boolean: bool | None = None
    expected_number: Decimal | None = None
    expected_text: str = ""


def parse_boolean_literal(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in _BOOLEAN_LITERAL_MAP:
        return _BOOLEAN_LITERAL_MAP[normalized]
    return None


def parse_decimal_literal(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None


def parse_legacy_condition(expression: str) -> tuple[StructuredCondition | None, str | None]:
    """Parse supported legacy condition expressions into structured values."""

    text = (expression or "").strip()
    if not text:
        return None, None

    match = _SIMPLE_CONDITION_PATTERN.match(text)
    if not match:
        return None, "Unsupported expression format."

    source = match.group("left").strip()
    operator = match.group("operator")
    right_text = match.group("right").strip()
    if not source:
        return None, "Missing condition source."
    if not right_text:
        return None, "Missing condition expected value."

    normalized_operator = "!=" if operator == "<>" else operator
    decimal_value = parse_decimal_literal(right_text)
    if decimal_value is not None:
        return (
            StructuredCondition(
                source=source,
                operator=normalized_operator,
                expected_number=decimal_value,
            ),
            None,
        )

    boolean_value = parse_boolean_literal(right_text)
    if boolean_value is not None:
        return (
            StructuredCondition(
                source=source,
                operator=normalized_operator,
                expected_boolean=boolean_value,
            ),
            None,
        )

    if len(right_text) >= 2 and right_text[0] == "'" and right_text[-1] == "'":
        return (
            StructuredCondition(
                source=source,
                operator=normalized_operator,
                expected_text=right_text[1:-1].replace("''", "'"),
            ),
            None,
        )

    return None, "Unsupported condition literal."
