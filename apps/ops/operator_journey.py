"""Helpers for guided operator journey progression."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.urls import reverse

from apps.groups.security import ensure_default_staff_groups

from .models import OperatorJourneyStep, OperatorJourneyStepCompletion


@dataclass(frozen=True)
class OperatorJourneyStatus:
    """Template-friendly status payload for dashboard rendering."""

    has_journey: bool
    is_complete: bool
    message: str
    task_title: str
    url: str


def next_step_for_user(*, user: AbstractBaseUser) -> OperatorJourneyStep | None:
    """Return the next required step for a user across active journey assignments."""

    if not user.is_authenticated:
        return None

    return (
        OperatorJourneyStep.objects.filter(
            is_active=True,
            journey__is_active=True,
            journey__security_group__in=_active_security_groups_for_user(user),
        )
        .exclude(completions__user=user)
        .select_related("journey")
        .order_by("journey__priority", "journey__name", "order", "id")
        .first()
    )


def complete_step_for_user(*, user: AbstractBaseUser, step: OperatorJourneyStep) -> bool:
    """Mark one step complete only when it is the user's next required step."""

    next_step = next_step_for_user(user=user)
    if next_step is None or next_step.pk != step.pk:
        return False

    OperatorJourneyStepCompletion.objects.get_or_create(user=user, step=step)
    return True


def status_for_user(*, user: AbstractBaseUser) -> OperatorJourneyStatus:
    """Build dashboard status text and URL for the signed-in user."""

    if not user.is_authenticated:
        return OperatorJourneyStatus(
            has_journey=False,
            is_complete=True,
            message="",
            task_title="",
            url="",
        )

    has_journey_steps = OperatorJourneyStep.objects.filter(
        is_active=True,
        journey__is_active=True,
        journey__security_group__in=_active_security_groups_for_user(user),
    )

    if not has_journey_steps.exists():
        return OperatorJourneyStatus(
            has_journey=False,
            is_complete=True,
            message="",
            task_title="",
            url="",
        )

    next_step = next_step_for_user(user=user)
    if next_step is None:
        return OperatorJourneyStatus(
            has_journey=True,
            is_complete=True,
            message="All Operator tasks completed to date. Keep coming back for more.",
            task_title="",
            url="",
        )

    return OperatorJourneyStatus(
        has_journey=True,
        is_complete=False,
        message=next_step.title,
        task_title=next_step.title,
        url=reverse("ops:operator-journey-step", args=[next_step.pk]),
    )


def _active_security_groups_for_user(user: AbstractBaseUser):
    if user.is_staff:
        ensure_default_staff_groups(user)
    return user.groups.all()
