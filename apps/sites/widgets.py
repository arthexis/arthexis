from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.widgets import register_widget
from apps.widgets.models import WidgetZone


@register_widget(
    slug="public-site-traffic",
    name=_("Public site traffic"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/public_site_traffic.html",
    description=_("Recent public site visits"),
)
def public_site_traffic_widget(**_kwargs):
    return {}
