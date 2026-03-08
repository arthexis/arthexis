"""Template tags exposing configured staff dashboard tasks."""

from __future__ import annotations

from django import template

from apps.actions.staff_tasks import visible_staff_tasks_for_user

register = template.Library()


@register.simple_tag(takes_context=True)
def admin_staff_tasks(context):
    """Return visible staff dashboard tasks for the current user."""

    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None:
        return []
    return visible_staff_tasks_for_user(user)
