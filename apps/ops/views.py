"""Views supporting in-progress operation banners."""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


@staff_member_required
def clear_active_operation(request: HttpRequest):
    """Clear the active operation from session storage."""

    request.session.pop("ops_active_operation_id", None)
    next_url = request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("admin:index")
    return HttpResponseRedirect(next_url)
