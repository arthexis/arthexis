"""Middleware to expose active operation context in admin templates."""

from __future__ import annotations

from .models import OperationScreen


class ActiveOperationMiddleware:
    """Attach active operation details from session to each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        operation_id = request.session.get("ops_active_operation_id")
        request.ops_active_operation = None
        if operation_id:
            request.ops_active_operation = OperationScreen.objects.filter(
                pk=operation_id,
                is_active=True,
            ).first()
            if request.ops_active_operation is None:
                request.session.pop("ops_active_operation_id", None)

        return self.get_response(request)
