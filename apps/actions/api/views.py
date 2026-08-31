"""HTTP endpoints for explicit supported actions."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@login_required
@require_GET
def security_groups(request: HttpRequest) -> JsonResponse:
    """List security groups for the authenticated session user.

    Parameters:
        request: Incoming Django request with an authenticated user.

    Returns:
        JSON response containing sorted security group names for the current user.
    """

    groups = list(request.user.groups.values_list("name", flat=True).order_by("name"))
    return JsonResponse({"groups": groups})
