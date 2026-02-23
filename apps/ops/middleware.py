"""Request middleware for operation workflow session tracking."""

from __future__ import annotations


class OperationSessionMiddleware:
    """Persist/clear active operation selection in the current user session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        clear_flag = request.GET.get("ops_clear")
        if clear_flag == "1":
            request.session.pop("ops_active_operation_id", None)

        operation_id = request.GET.get("ops")
        if operation_id and operation_id.isdigit():
            request.session["ops_active_operation_id"] = int(operation_id)

        return self.get_response(request)
