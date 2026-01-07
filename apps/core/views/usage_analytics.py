from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse

from apps.core.analytics import build_usage_summary
from apps.core.models import UsageEvent


@staff_member_required
def usage_analytics_summary(request):
    days_param = request.GET.get("days")
    try:
        days = int(days_param) if days_param else 30
    except (TypeError, ValueError):
        days = 30

    data = build_usage_summary(days=days, queryset=UsageEvent.objects.all())
    return JsonResponse(data, safe=False)
