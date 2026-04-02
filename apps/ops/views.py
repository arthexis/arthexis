"""Views supporting in-progress operation banners."""

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse

from .redirects import safe_host_redirect
from .status_surface import build_status_surface, scoped_log_excerpts


@staff_member_required
def clear_active_operation(request: HttpRequest):
    """Clear the active operation from session storage."""

    request.session.pop("ops_active_operation_id", None)
    next_url = request.GET.get("next") or ""
    return safe_host_redirect(request, next_url)


@login_required
def status_surface(request: HttpRequest) -> JsonResponse:
    """Return role-aware operational status, events, and redacted log excerpts."""

    return JsonResponse(build_status_surface(user=request.user), safe=False)


@login_required
def status_log_excerpts(request: HttpRequest) -> JsonResponse:
    """Return only scoped log excerpts for clients polling the status surface."""

    return JsonResponse({"log_excerpts": scoped_log_excerpts(user=request.user)})
