from __future__ import annotations

import base64
import ipaddress
import logging
from io import BytesIO
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import qrcode
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.http.request import validate_host
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt

from .models import EmbedLead

logger = logging.getLogger(__name__)


def _build_share_url(target_url: str, user: object | None) -> str:
    username = getattr(user, "username", "") if getattr(user, "is_authenticated", False) else ""
    if not username:
        return target_url

    parsed = urlparse(target_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("ref", username))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _encode_qr_image(url: str) -> str:
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b1420", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_page_meta(
    target_url: str, parsed_target, override_title: str = "", override_subtitle: str = ""
):
    hostname = parsed_target.hostname or ""
    clean_path = parsed_target.path if parsed_target.path not in {"", "/"} else ""
    title = override_title or clean_path or hostname or target_url
    fragment = override_subtitle or parsed_target.fragment or ""
    favicon_url = None
    if parsed_target.scheme and parsed_target.netloc:
        favicon_url = f"{parsed_target.scheme}://{parsed_target.netloc}/favicon.ico"
    return title, fragment, favicon_url


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

    provided_title = request.GET.get("title", "").strip()
    provided_subtitle = request.GET.get("subtitle", "").strip()

    parsed_target = urlparse(target)
    is_absolute = parsed_target.scheme in {"http", "https"}
    if not is_absolute and not target.startswith("/"):
        return HttpResponseBadRequest(_("The target must be a valid URL or path."))

    if is_absolute:
        allowed_hosts: set[str] = set(settings.ALLOWED_HOSTS or [])
        if parsed_target.hostname:
            allowed_hosts.add(parsed_target.hostname)
        if parsed_target.netloc:
            allowed_hosts.add(parsed_target.netloc)
        try:
            allowed_hosts.add(request.get_host())
        except Exception:  # pragma: no cover - defensive, request host should be valid
            pass

        referer_host = urlparse(request.META.get("HTTP_REFERER", "")).hostname
        if referer_host:
            allowed_hosts.add(referer_host)

        if not url_has_allowed_host_and_scheme(target, allowed_hosts=allowed_hosts):
            if not validate_host(parsed_target.netloc, allowed_hosts):
                return HttpResponseBadRequest(_("The target URL is not allowed."))

    target_url = target if is_absolute else request.build_absolute_uri(target)
    parsed_target = urlparse(target_url)

    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        user = None

    share_url = _build_share_url(target_url, user)
    page_title, subtitle, favicon_url = _extract_page_meta(
        target_url, parsed_target, provided_title, provided_subtitle
    )
    qr_code_data = _encode_qr_image(share_url)

    referer = request.META.get("HTTP_REFERER", "") or ""
    user_agent = request.META.get("HTTP_USER_AGENT", "") or ""
    ip_address = _extract_client_ip(request) or None
    share_referer = getattr(user, "username", "") if user else ""

    try:
        EmbedLead.objects.create(
            target_url=target_url,
            user=user,
            path=request.get_full_path(),
            referer=referer,
            user_agent=user_agent,
            ip_address=ip_address,
            share_referer=share_referer,
        )
    except Exception:  # pragma: no cover - best effort logging
        logger.debug("Failed to record EmbedLead for %s", target_url, exc_info=True)

    context = {
        "target_url": target_url,
        "share_url": share_url,
        "page_title": page_title,
        "subtitle": subtitle,
        "qr_code_data": qr_code_data,
        "favicon_url": favicon_url,
    }
    return render(request, "embeds/embed.html", context)
