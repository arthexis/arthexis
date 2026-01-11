from __future__ import annotations

import re
from collections.abc import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import EmailValidator

_SPLIT_RE = re.compile(r"[\s,]+")


def _iter_recipient_candidates(recipients: Iterable[str] | str | None) -> Iterable[str]:
    if recipients is None:
        return []
    if isinstance(recipients, str):
        return (part for part in _SPLIT_RE.split(recipients) if part)
    return recipients


def normalize_recipients(
    recipients: Iterable[str] | str | None,
    *,
    validate: bool = False,
    validator: EmailValidator | None = None,
) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    validator = validator or (EmailValidator() if validate else None)

    for candidate in _iter_recipient_candidates(recipients):
        email = (candidate or "").strip()
        if not email:
            continue
        if validator is not None:
            validator(email)
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(email)

    return normalized


def resolve_recipient_fallbacks(
    recipients: Iterable[str] | str | None,
    *,
    owner=None,
    validate: bool = False,
    validator: EmailValidator | None = None,
    include_owner_cc: bool = False,
) -> tuple[list[str], list[str]]:
    validator = validator or (EmailValidator() if validate else None)
    to = normalize_recipients(recipients, validator=validator)
    cc: list[str] = []

    owner_email = ""
    if owner is not None and getattr(owner, "email", None):
        owner_email = (owner.email or "").strip()
    owner_list = normalize_recipients([owner_email] if owner_email else [], validator=validator)

    if to:
        if include_owner_cc and owner_list:
            to_keys = {email.lower() for email in to}
            for email in owner_list:
                if email.lower() not in to_keys:
                    cc.append(email)
        return to, cc

    if owner_list:
        return owner_list, cc

    admin_emails = list(
        get_user_model()
        .objects.filter(is_superuser=True, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    if admin_emails:
        return normalize_recipients(admin_emails, validator=validator), cc

    fallback = (settings.DEFAULT_FROM_EMAIL or "").strip()
    if fallback:
        return normalize_recipients([fallback], validator=validator), cc

    return [], cc
