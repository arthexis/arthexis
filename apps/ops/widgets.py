"""Widgets exposed by the operations app."""

from django.utils.translation import gettext_lazy as _

from apps.widgets import register_widget
from apps.widgets.models import WidgetZone

from .security_alerts import build_security_alerts


def _is_authenticated_staff(*, request, **_kwargs) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


@register_widget(
    slug="security-alerts",
    name=_("Security alerts"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/security_alerts.html",
    description=_("Critical operational and security readiness alerts."),
    order=5,
    permission=_is_authenticated_staff,
)
def security_alerts_widget(**_kwargs):
    """Render normalized security alerts for the sidebar."""

    return {"alerts": build_security_alerts()}
