from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.core import changelog
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


@register_widget(
    slug="latest-updates",
    name=_("Latest updates"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/latest_updates.html",
    description=_("Recent commit summaries from the changelog"),
    order=40,
)
def latest_updates_widget(**_kwargs):
    commits = changelog.get_latest_commits(limit=3)
    return {"commits": commits}
