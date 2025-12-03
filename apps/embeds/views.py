from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt

from .models import EmbedLead

logger = logging.getLogger(__name__)


def _extract_client_ip(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates: list[str] = []
    if forwarded:
        candidates.extend(part.strip() for part in forwarded.split(","))
    remote = request.META.get("REMOTE_ADDR", "").strip()
    if remote:
        candidates.append(remote)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return candidate
    return ""


@xframe_options_exempt
def embed_card(request: HttpRequest) -> HttpResponse:
    target = request.GET.get("target", "").strip()
    if not target:
        return HttpResponseBadRequest(_("A target URL is required."))

    is_absolute = urlparse(target).scheme in {"http", "https"}
    if not is_absolute and not target.startswith("/"):
        return HttpResponseBadRequest(_("The target must be a valid URL or path."))

    if is_absolute and not url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        return HttpResponseBadRequest(_("The target URL is not allowed."))

    target_url = target if is_absolute else request.build_absolute_uri(target)

    referer = request.META.get("HTTP_REFERER", "") or ""
    user_agent = request.META.get("HTTP_USER_AGENT", "") or ""
    ip_address = _extract_client_ip(request) or None
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        user = None

    try:
        EmbedLead.objects.create(
            target_url=target_url,
            user=user,
            path=request.get_full_path(),
            referer=referer,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    except Exception:  # pragma: no cover - best effort logging
        logger.debug("Failed to record EmbedLead for %s", target_url, exc_info=True)

    context = {
        "target_url": target_url,
    }
    return render(request, "embeds/embed.html", context)
