"""Service helpers for operations state and pending calculations."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.nodes.models import Node

from .models import OperationExecution, OperationScreen


def pending_operations_for_user(user, *, node: Node | None = None):
    """Return pending operation queryset for a staff user and optional node."""

    if not user or not getattr(user, "is_authenticated", False):
        return OperationScreen.objects.none()

    screens = OperationScreen.objects.filter(is_active=True)
    if not user.is_staff:
        screens = screens.filter(is_required=False)

    user_exec = OperationExecution.objects.filter(
        operation=OuterRef("pk"),
        user=user,
    ).order_by("-completed_at")
    screens = screens.annotate(last_completed_at=Subquery(user_exec.values("completed_at")[:1]))

    if node is not None:
        node_exec = OperationExecution.objects.filter(
            operation=OuterRef("pk"),
            node=node,
        ).order_by("-completed_at")
        screens = screens.annotate(last_node_completed_at=Subquery(node_exec.values("completed_at")[:1]))

    now = timezone.now()
    pending_ids: list[int] = []
    for op in screens:
        if op.scope == OperationScreen.Scope.NODE and node is not None:
            last = getattr(op, "last_node_completed_at", None)
        else:
            last = getattr(op, "last_completed_at", None)

        if last is None:
            pending_ids.append(op.pk)
            continue

        if op.expires_after_days:
            expires_at = last + timedelta(days=op.expires_after_days)
            if expires_at <= now:
                pending_ids.append(op.pk)

    return OperationScreen.objects.filter(pk__in=pending_ids).order_by("priority", "title")


def count_required_pending_operations() -> int:
    """Return total number of required operations not completed by staff users."""

    User = get_user_model()
    staff_users = User.objects.filter(is_staff=True, is_active=True)
    required = OperationScreen.objects.filter(is_active=True, is_required=True)
    count = 0
    for user in staff_users:
        count += pending_operations_for_user(user).filter(pk__in=required.values("pk")).count()
    return count
