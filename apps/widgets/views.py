from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse

from apps.widgets.services import render_zone_html


@staff_member_required
def zone_widget_html(request: HttpRequest, zone_slug: str) -> HttpResponse:
    html = render_zone_html(request=request, zone_slug=zone_slug)
    return HttpResponse(html)
