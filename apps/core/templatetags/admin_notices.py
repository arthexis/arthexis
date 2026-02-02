from __future__ import annotations

from django import template
from django.db import DatabaseError

from apps.core.models import AdminNotice

register = template.Library()


@register.simple_tag
def latest_admin_notice():
    try:
        notice = (
            AdminNotice.objects.filter(dismissed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
    except DatabaseError:
        return None

    return notice
