from __future__ import annotations

import re
from dataclasses import dataclass

VERSION_BUMP_NONE = "none"
VERSION_BUMP_PATCH = "patch"
VERSION_BUMP_MINOR = "minor"
VERSION_BUMP_MAJOR = "major"
VERSION_BUMP_UNKNOWN = "unknown"

UPGRADE_CHANNEL_STABLE = "stable"
UPGRADE_CHANNEL_REGULAR = "regular"
UPGRADE_CHANNEL_UNSTABLE = "unstable"
UPGRADE_CHANNEL_CUSTOM = "custom"

AUTO_UPGRADE_DAY_MINUTES = 1440
AUTO_UPGRADE_WEEK_MINUTES = 10080
AUTO_UPGRADE_MONTH_MINUTES = 43200

UPGRADE_CHANNEL_ALIASES = {
    "stable": UPGRADE_CHANNEL_STABLE,
    "lts": UPGRADE_CHANNEL_STABLE,
    "regular": UPGRADE_CHANNEL_REGULAR,
    "normal": UPGRADE_CHANNEL_REGULAR,
    "version": UPGRADE_CHANNEL_REGULAR,
    "latest": UPGRADE_CHANNEL_UNSTABLE,
    "unstable": UPGRADE_CHANNEL_UNSTABLE,
    "custom": UPGRADE_CHANNEL_CUSTOM,
}

AUTO_UPGRADE_BUMP_CADENCES = {
    UPGRADE_CHANNEL_STABLE: {
        VERSION_BUMP_PATCH: AUTO_UPGRADE_WEEK_MINUTES,
    },
    UPGRADE_CHANNEL_REGULAR: {
        VERSION_BUMP_PATCH: AUTO_UPGRADE_DAY_MINUTES,
        VERSION_BUMP_MINOR: AUTO_UPGRADE_DAY_MINUTES,
        VERSION_BUMP_MAJOR: AUTO_UPGRADE_WEEK_MINUTES,
    },
}

AUTO_UPGRADE_LIVE_CADENCE = AUTO_UPGRADE_DAY_MINUTES

_VERSION_RE = re.compile(
    r"^\s*v?(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?"
    r"(?:\.(?P<patch>\d+))?"
)


@dataclass(frozen=True)
class ParsedVersion:
    major: int
    minor: int = 0
    patch: int = 0


def normalize_upgrade_channel(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None
    return UPGRADE_CHANNEL_ALIASES.get(normalized, normalized)


def parse_version(value: str | None) -> ParsedVersion | None:
    if value is None:
        return None

    match = _VERSION_RE.match(value)
    if not match:
        return None

    return ParsedVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor") or 0),
        patch=int(match.group("patch") or 0),
    )


def classify_version_bump(
    local_version: str | None,
    target_version: str | None,
) -> str:
    local = parse_version(local_version)
    target = parse_version(target_version)
    if local is None or target is None:
        return VERSION_BUMP_UNKNOWN

    if (target.major, target.minor, target.patch) < (
        local.major,
        local.minor,
        local.patch,
    ):
        return VERSION_BUMP_UNKNOWN

    if target.major != local.major:
        return VERSION_BUMP_MAJOR
    if target.minor != local.minor:
        return VERSION_BUMP_MINOR
    if target.patch != local.patch:
        return VERSION_BUMP_PATCH
    return VERSION_BUMP_NONE


def auto_upgrade_bump_cadence_minutes(channel: str, bump: str) -> int | None:
    normalized_channel = normalize_upgrade_channel(channel)
    if normalized_channel == UPGRADE_CHANNEL_UNSTABLE:
        return AUTO_UPGRADE_LIVE_CADENCE
    if normalized_channel is None:
        return None
    return AUTO_UPGRADE_BUMP_CADENCES.get(normalized_channel, {}).get(bump)


def auto_upgrade_bump_allowed(channel: str, bump: str) -> bool:
    normalized_channel = normalize_upgrade_channel(channel)
    if normalized_channel == UPGRADE_CHANNEL_UNSTABLE:
        return True
    if bump == VERSION_BUMP_NONE:
        return False
    if normalized_channel is None:
        return False
    return bump in AUTO_UPGRADE_BUMP_CADENCES.get(normalized_channel, {})
