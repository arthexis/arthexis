"""Helpers for guided operator journey progression."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.urls import reverse

from apps.groups.security import ensure_default_staff_groups

from .models import OperatorJourneyStep, OperatorJourneyStepCompletion

ROLE_VALIDATION_STEP_SLUG = "validate-local-node-role"
PROVISION_SUPERUSER_STEP_SLUG = "provision-ops-superuser"
ONE_TIME_STEP_SLUGS = {PROVISION_SUPERUSER_STEP_SLUG, ROLE_VALIDATION_STEP_SLUG}
BOOTSTRAP_ADMIN_USERNAMES = {"admin", "admi"}


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

    remaining_steps = (
        OperatorJourneyStep.objects.filter(
            is_active=True,
            journey__is_active=True,
            journey__security_group__in=_active_security_groups_for_user(user),
        )
        .exclude(completions__user=user)
        .select_related("journey")
        .order_by("journey__priority", "journey__name", "order", "id")
    )
    for step in remaining_steps:
        if _step_is_already_satisfied(user=user, step=step):
            OperatorJourneyStepCompletion.objects.get_or_create(user=user, step=step)
            continue
        return step
    return None


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


def _step_is_already_satisfied(*, user: AbstractBaseUser, step: OperatorJourneyStep) -> bool:
    """Return whether ``step`` was already completed at node level."""

    if step.slug in ONE_TIME_STEP_SLUGS and step.completions.exists():
        return True
    if step.slug == ROLE_VALIDATION_STEP_SLUG:
        return _local_node_role_is_available()
    if step.slug == PROVISION_SUPERUSER_STEP_SLUG:
        return _current_user_is_operational_staff(user=user)
    return False


def _local_node_role_is_available() -> bool:
    try:
        from apps.nodes.models import Node
    except (ImportError, LookupError):
        return False

    local_node = Node.get_local()
    return bool(local_node and getattr(local_node, "role_id", None))


def _current_user_is_operational_staff(*, user: AbstractBaseUser) -> bool:
    if not user.is_authenticated or not user.is_staff or user.is_superuser:
        return False
    if getattr(user, "is_deleted", False):
        return False
    return (user.username or "").strip().lower() not in BOOTSTRAP_ADMIN_USERNAMES
