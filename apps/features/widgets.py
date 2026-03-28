from __future__ import annotations

from collections import OrderedDict

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.widgets import register_widget
from apps.widgets.models import WidgetZone

from .models import Feature

LATEST_FEATURE_UPDATES_ORDER = 30


@register_widget(
    slug="latest-feature-updates",
    name=_("Latest feature updates"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/latest_feature_updates.html",
    description=_("Active suite features grouped by owning app."),
    order=LATEST_FEATURE_UPDATES_ORDER,
)
def latest_feature_updates_widget(**_kwargs):
    features = (
        Feature.objects.select_related("main_app")
        .filter(is_enabled=True)
        .order_by("main_app__name", "display")
    )
    app_entries: OrderedDict[str, dict[str, object]] = OrderedDict()
    for feature in features:
        admin_url = reverse(
            "admin:features_feature_change",
            args=[feature.pk],
        )
        app_name = feature.main_app.display_name if feature.main_app_id else _("Unassigned")
        app_key = feature.main_app.name if feature.main_app_id else ""
        entry = app_entries.setdefault(
            app_key,
            {
                "app_name": app_name,
                "features": [],
            },
        )
        entry["features"].append(
            {
                "display": feature.display,
                "admin_url": admin_url,
            }
        )
    return {
        "app_entries": list(app_entries.values()),
        "feature_admin_url": reverse("admin:features_feature_changelist"),
        "feature_disabled_admin_url": (
            f"{reverse('admin:features_feature_changelist')}?is_enabled__exact=0"
        ),
    }
