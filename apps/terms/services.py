from __future__ import annotations

from django.db import transaction

from .forms import TermAcceptanceForm
from .models import Term, TermAcceptance


def record_acceptance(
    *,
    term: Term,
    user=None,
    submission=None,
    data=None,
    files=None,
    ip_address="",
    user_agent="",
) -> TermAcceptance:
    """Record acceptance for a term using the posted payload."""

    form = TermAcceptanceForm(term, data=data, files=files)
    form.is_valid()
    if form.errors:
        raise ValueError(form.errors)
    with transaction.atomic():
        return form.save(
            user=user,
            submission=submission,
            ip_address=ip_address,
            user_agent=user_agent,
        )
