from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone

WORKGROUP_WORDS: tuple[str, ...] = (
    "amber",
    "anchor",
    "apex",
    "archive",
    "atlas",
    "aurora",
    "bastion",
    "beacon",
    "binary",
    "bravo",
    "canvas",
    "cedar",
    "cipher",
    "cobalt",
    "comet",
    "copper",
    "delta",
    "ember",
    "fabric",
    "fathom",
    "forge",
    "garden",
    "harbor",
    "haven",
    "helios",
    "horizon",
    "index",
    "ivory",
    "juniper",
    "kernel",
    "lattice",
    "ledger",
    "magnet",
    "matrix",
    "meridian",
    "mirror",
    "nexus",
    "nickel",
    "nova",
    "onyx",
    "orbit",
    "parity",
    "quartz",
    "radial",
    "relay",
    "ripple",
    "signal",
    "silver",
    "summit",
    "syntax",
    "tempo",
    "thread",
    "titan",
    "topaz",
    "vector",
    "velvet",
    "vertex",
    "violet",
    "wander",
    "window",
    "xenon",
    "yellow",
    "zenith",
    "zircon",
)


@dataclass(frozen=True)
class WorkgroupPassword:
    """Daily password plus local-day validity [valid_from, valid_until) in timezone_name."""
    password: str
    first_word: str
    second_word: str
    date: dt.date
    timezone_name: str
    valid_from: dt.datetime
    valid_until: dt.datetime


def _timezone_name() -> str:
    return (
        getattr(settings, "WORKGROUP_DAILY_PASSWORD_TIMEZONE", "")
        or getattr(settings, "TIME_ZONE", "")
        or "UTC"
    )


def _zoneinfo() -> ZoneInfo:
    name = _timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _password_seed() -> str:
    return (
        getattr(settings, "WORKGROUP_DAILY_PASSWORD_SEED", "")
        or getattr(settings, "SECRET_KEY", "")
        or "arthexis-workgroup"
    )


def password_for_date(day: dt.date, *, seed: str | None = None) -> str:
    """Return the two-word workgroup password for a local calendar date."""

    secret = (seed if seed is not None else _password_seed()).encode("utf-8")
    digest = hmac.new(
        secret,
        f"workgroup-password:{day.isoformat()}".encode(),
        hashlib.sha256,
    ).digest()
    first = int.from_bytes(digest[:4], "big") % len(WORKGROUP_WORDS)
    second = int.from_bytes(digest[4:8], "big") % len(WORKGROUP_WORDS)
    if first == second:
        second = (second + 1) % len(WORKGROUP_WORDS)
    return f"{WORKGROUP_WORDS[first]}-{WORKGROUP_WORDS[second]}"


def password_record_for_date(day: dt.date) -> WorkgroupPassword:
    """Return the password record for local calendar `day` in configured timezone."""
    zone = _zoneinfo()
    valid_from = dt.datetime.combine(day, dt.time.min, tzinfo=zone)
    valid_until = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=zone)
    password = password_for_date(day)
    first, second = password.split("-", 1)
    return WorkgroupPassword(
        password=password,
        first_word=first,
        second_word=second,
        date=day,
        timezone_name=zone.key,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def current_password(now: dt.datetime | None = None) -> WorkgroupPassword:
    """Return the current local-day password record in the configured timezone."""
    zone = _zoneinfo()
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current, zone)
    current = current.astimezone(zone)
    return password_record_for_date(current.date())
