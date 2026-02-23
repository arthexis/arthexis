"""Widgets exposed by the operations app."""

from django.utils.translation import gettext_lazy as _

from apps.widgets import register_widget
from apps.widgets.models import WidgetZone

from .models import pending_operations_for_user


def _can_view_pending_ops(*, request, **_kwargs) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


@register_widget(
    slug="pending-operations",
    name=_("Pending operations"),
    zone=WidgetZone.ZONE_SIDEBAR,
    template_name="widgets/pending_operations.html",
    description=_("Top pending operations by priority for the current user."),
    order=10,
    permission=_can_view_pending_ops,
)
def pending_operations_widget(*, request, **_kwargs):
    """Render the pending operations widget context."""

    pending = pending_operations_for_user(request.user)
    return {"pending_operations": pending[:3], "pending_count": len(pending)}
