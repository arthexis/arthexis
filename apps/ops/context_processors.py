"""Template context helpers for active operation session state."""

from __future__ import annotations

from .models import OperationScreen


def active_operation(request):
    """Expose active operation metadata to admin templates."""

    operation_id = request.session.get("ops_active_operation_id")
    if not operation_id:
        return {"active_operation": None}

    operation = OperationScreen.objects.filter(pk=operation_id, is_active=True).first()
    if operation is None:
        request.session.pop("ops_active_operation_id", None)
    return {"active_operation": operation}
