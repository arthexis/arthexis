from __future__ import annotations

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

from config.request_utils import is_https_request


def _safe_next_url(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return None

    allowed_hosts = {request.get_host()}
    allowed_hosts.update(filter(None, settings.ALLOWED_HOSTS))

    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts=allowed_hosts,
        require_https=is_https_request(request),
    ):
        return candidate
    return None
