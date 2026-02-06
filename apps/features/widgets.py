from __future__ import annotations

from django.db.models import DateTimeField, F, OuterRef, Subquery
from django.db.models.functions import Coalesce, Greatest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.widgets import register_widget
from apps.widgets.models import WidgetZone

from .models import FeatureNote, Feature

LATEST_FEATURE_UPDATES_LIMIT = 3
LATEST_FEATURE_UPDATES_ORDER = 30


@register_widget(
    slug="latest-feature-updates",
    name=_("Latest feature updates"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/latest_feature_updates.html",
    description=_("Recently updated features, including note changes."),
    order=LATEST_FEATURE_UPDATES_ORDER,
)
def latest_feature_updates_widget(**_kwargs):
    latest_note_updated = FeatureNote.objects.filter(
        feature=OuterRef("pk")
    ).order_by("-updated_at")
    features = (
        Feature.objects.annotate(
            latest_note_updated_at=Subquery(
                latest_note_updated.values("updated_at")[:1],
                output_field=DateTimeField(),
            ),
        )
        .annotate(
            latest_activity_at=Greatest(
                F("updated_at"),
                Coalesce(F("latest_note_updated_at"), F("updated_at")),
            ),
        )
        .order_by("-latest_activity_at", "-updated_at", "display")[:LATEST_FEATURE_UPDATES_LIMIT]
    )
    entries = []
    for feature in features:
        admin_url = reverse(
            "admin:features_feature_change",
            args=[feature.pk],
        )
        toggle_url = reverse(
            "admin:features_feature_toggle",
            args=[feature.pk],
        )
        entries.append(
            {
                "feature": feature,
                "admin_url": admin_url,
                "site_url": feature.get_absolute_url(),
                "toggle_url": toggle_url,
                "updated_at": feature.latest_activity_at,
            }
        )
    return {
        "features": entries,
        "feature_admin_url": reverse("admin:features_feature_changelist"),
    }
