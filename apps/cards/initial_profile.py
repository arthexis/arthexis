"""Validation helpers for private first-boot RFID TOML profiles."""

from __future__ import annotations

from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

from django.core.exceptions import ValidationError

from apps.cards.models import RFID


class InitialProfileError(ValueError):
    """Raised when an initial RFID profile cannot be safely applied."""


def load_initial_profile_data(profile_path: Path) -> dict[str, object]:
    """Load an initial TOML profile without applying any configuration."""

    try:
        profile = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InitialProfileError(
            f"Initial RFID profile was not found: {profile_path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise InitialProfileError(f"Initial RFID profile is not valid TOML: {exc}") from exc
    if not isinstance(profile, dict):  # pragma: no cover - tomllib returns a dict
        raise InitialProfileError("Initial RFID profile must be a TOML table.")
    return profile


def load_pre_registered_rfids(profile_path: Path) -> tuple[str, ...]:
    """Return normalized, unique RFID values from an initial TOML profile."""

    profile = load_initial_profile_data(profile_path)

    if "rfid" not in profile:
        raise InitialProfileError("Initial RFID profile must contain an [rfid] table.")
    rfid_section = profile["rfid"]
    if not isinstance(rfid_section, dict):
        raise InitialProfileError("Initial RFID profile [rfid] section must be a table.")
    configured_rfids = rfid_section.get("pre_register", [])
    if not isinstance(configured_rfids, list):
        raise InitialProfileError("Initial RFID profile rfid.pre_register must be an array.")

    normalized_rfids: list[str] = []
    rfid_field = RFID._meta.get_field("rfid")
    for raw_rfid in configured_rfids:
        if not isinstance(raw_rfid, str):
            raise InitialProfileError("Initial RFID profile values must be strings.")
        normalized = RFID.normalize_code(raw_rfid)
        if not normalized:
            raise InitialProfileError("Initial RFID profile contains an empty RFID value.")
        if len(normalized) > rfid_field.max_length:
            raise InitialProfileError(
                "Initial RFID profile contains an RFID value that is too long."
            )
        try:
            rfid_field.run_validators(normalized)
        except ValidationError as exc:
            raise InitialProfileError(
                "Initial RFID profile contains an invalid RFID value."
            ) from exc
        if normalized not in normalized_rfids:
            normalized_rfids.append(normalized)
    return tuple(normalized_rfids)
