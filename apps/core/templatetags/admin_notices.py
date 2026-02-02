from __future__ import annotations

from django import template
from django.db import DatabaseError

from apps.core.models import AdminNotice

register = template.Library()


@register.simple_tag
def latest_admin_notice():
    try:
        notice = AdminNotice.objects.order_by("-created_at").first()
    except DatabaseError:
        return None

    if not notice or notice.dismissed_at:
        return None
    return notice
