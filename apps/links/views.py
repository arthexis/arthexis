from __future__ import annotations

import base64
import logging
from io import BytesIO
from urllib.parse import urlparse

import qrcode
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .models import QRRedirect, QRRedirectLead

logger = logging.getLogger(__name__)


def _resolve_target_url(request: HttpRequest, target_url: str) -> str:
    target_url = (target_url or "").strip()
    parsed = urlparse(target_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if not url_has_allowed_host_and_scheme(target_url, allowed_hosts={parsed.netloc}):
            raise ValueError("Unsafe redirect target")
        return target_url
    if target_url.startswith("/") and not parsed.scheme and not parsed.netloc:
        return request.build_absolute_uri(target_url)
    raise ValueError("Invalid redirect target")


def _encode_qr_image(url: str) -> str:
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b1420", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_client_ip(request: HttpRequest) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates: list[str] = []
    if forwarded:
        candidates.extend(part.strip() for part in forwarded.split(","))
    remote = request.META.get("REMOTE_ADDR", "").strip()
    if remote:
        candidates.append(remote)
    for candidate in candidates:
        if candidate:
            return candidate
    return ""


def qr_redirect(request: HttpRequest, slug: str) -> HttpResponse:
    qr_entry = get_object_or_404(QRRedirect, slug=slug)
    try:
        target_url = _resolve_target_url(request, qr_entry.target_url)
    except ValueError:
        return HttpResponseBadRequest("Invalid redirect target.")
    return redirect(target_url)


def qr_redirect_public_view(request: HttpRequest, slug: str) -> HttpResponse:
    qr_entry = get_object_or_404(QRRedirect, slug=slug)
    if not qr_entry.is_public and not request.user.is_staff:
        raise Http404("QR entry not available.")
    try:
        iframe_url = _resolve_target_url(request, qr_entry.target_url)
    except ValueError:
        return HttpResponseBadRequest("Invalid redirect target.")

    if request.user.is_staff:
        sidebar_qs = QRRedirect.objects.all()
    else:
        sidebar_qs = QRRedirect.objects.filter(is_public=True)
    sidebar_entries = sidebar_qs.order_by("title", "slug").only("slug", "title", "pk")

    qr_url = request.build_absolute_uri(qr_entry.redirect_path())
    qr_data_uri = f"data:image/png;base64,{_encode_qr_image(qr_url)}"

    try:
        QRRedirectLead.objects.create(
            qr_redirect=qr_entry,
            target_url=iframe_url,
            user=request.user if request.user.is_authenticated else None,
            path=request.get_full_path(),
            referer=request.META.get("HTTP_REFERER", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            ip_address=_extract_client_ip(request),
        )
    except Exception:  # pragma: no cover - best effort logging
        logger.debug("Failed to record QRRedirectLead for %s", qr_entry.slug, exc_info=True)

    context = {
        "qr_entry": qr_entry,
        "iframe_url": iframe_url,
        "qr_data_uri": qr_data_uri,
        "sidebar_entries": sidebar_entries,
        "page_title": qr_entry.title or qr_entry.slug,
    }
    return render(request, "links/qr_redirect_public.html", context)
