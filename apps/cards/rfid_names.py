"""Deterministic human-readable names for RFID scan labels."""

from __future__ import annotations

import hashlib
import re

NAME_NAMESPACE = "arthexis-rfid-name-v1"
DROP_SUFFIX_HEX_CHARS = 4
RFID_HEX_RE = re.compile(r"[^0-9A-F]+")

ADJECTIVES = (
    "Amber",
    "Azure",
    "Brave",
    "Bright",
    "Calm",
    "Clear",
    "Clever",
    "Cool",
    "Cosmic",
    "Crimson",
    "Daring",
    "Dawn",
    "Deep",
    "Drift",
    "Dusty",
    "Easy",
    "Fair",
    "Fast",
    "Fine",
    "Fresh",
    "Gentle",
    "Golden",
    "Grand",
    "Green",
    "Happy",
    "Humble",
    "Icy",
    "Jolly",
    "Kind",
    "Lively",
    "Lucky",
    "Lunar",
    "Magic",
    "Mellow",
    "Merry",
    "Misty",
    "Nimble",
    "Noble",
    "Open",
    "Pale",
    "Prime",
    "Proud",
    "Quick",
    "Quiet",
    "Rapid",
    "Ready",
    "Regal",
    "Royal",
    "Ruby",
    "Sharp",
    "Silver",
    "Solar",
    "Solid",
    "Swift",
    "True",
    "Urban",
    "Vivid",
    "Warm",
    "Wise",
    "Young",
    "Zen",
)

NOUNS = (
    "Anchor",
    "Arbor",
    "Beacon",
    "Birch",
    "Bloom",
    "Brook",
    "Canyon",
    "Cedar",
    "Circle",
    "Clover",
    "Comet",
    "Creek",
    "Crown",
    "Delta",
    "Ember",
    "Field",
    "Fjord",
    "Forge",
    "Grove",
    "Harbor",
    "Haven",
    "Iris",
    "Jewel",
    "Lamp",
    "Leaf",
    "Mesa",
    "Moon",
    "North",
    "Nova",
    "Oak",
    "Opal",
    "Orbit",
    "Pearl",
    "Pine",
    "Quartz",
    "River",
    "Root",
    "Sage",
    "Shore",
    "Sky",
    "Spark",
    "Stone",
    "Sun",
    "Thorn",
    "Trail",
    "Vale",
    "Vine",
    "Wave",
    "West",
    "Willow",
    "Wind",
    "Wing",
)


def normalize_rfid_for_name(value: object) -> str:
    """Return an uppercase hex RFID value for name generation."""

    return RFID_HEX_RE.sub("", str(value or "").strip().upper())


def rfid_name_key(value: object) -> str:
    """Return the charger-compatible RFID identity used for generated names."""

    normalized = normalize_rfid_for_name(value)
    truncated = normalized[:-DROP_SUFFIX_HEX_CHARS]
    return truncated or normalized


def stable_rfid_label(name_key: object, *, counter: int = 0) -> str:
    """Return a deterministic ``AdjNoun###`` label for ``name_key``."""

    normalized_key = normalize_rfid_for_name(name_key)
    if not normalized_key:
        return ""
    seed = f"{NAME_NAMESPACE}:{normalized_key}:{counter}".encode("ascii")
    digest = hashlib.sha256(seed).digest()
    adjective = ADJECTIVES[int.from_bytes(digest[0:4], "big") % len(ADJECTIVES)]
    noun = NOUNS[int.from_bytes(digest[4:8], "big") % len(NOUNS)]
    digits = int.from_bytes(digest[8:12], "big") % 1000
    return f"{adjective}{noun}{digits:03d}"


def generated_label_for_rfid(value: object, *, counter: int = 0) -> str:
    """Return the generated label for a full RFID value."""

    return stable_rfid_label(rfid_name_key(value), counter=counter)
