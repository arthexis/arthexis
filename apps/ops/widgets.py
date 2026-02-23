"""Dashboard widgets for operation visibility in admin."""

from __future__ import annotations

from apps.widgets.registry import register_widget

from .services import pending_operations_for_user


def _staff_permission(*, request, **_kwargs) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


@register_widget(
    slug="ops_pending_operations",
    name="Pending Operations",
    zone="sidebar",
    zone_name="Sidebar",
    template_name="admin/widgets/pending_operations.html",
    description="Top pending operations for the current user.",
    order=15,
    permission=_staff_permission,
)
def pending_operations_widget(*, request, **_kwargs):
    """Render the pending operations summary for the current user."""

    pending = list(pending_operations_for_user(request.user)[:3])
    return {"pending_operations": pending}
