"""Views supporting in-progress operation banners."""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse


@staff_member_required
def clear_active_operation(request: HttpRequest):
    """Clear the active operation from session storage."""

    request.session.pop("ops_active_operation_id", None)
    next_url = request.GET.get("next") or reverse("admin:index")
    return HttpResponseRedirect(next_url)
