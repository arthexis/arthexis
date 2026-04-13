"""Thin helpers that adapt django-otp's TOTP device model for Arthexis."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterable
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import models
from django.utils.translation import gettext_lazy as _

from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.util import random_hex


def generate_totp_key() -> str:
    return random_hex(20)


def totp_base32_key(device: TOTPDevice) -> str:
    return base64.b32encode(device.bin_key).decode("utf-8").strip("=")


def get_totp_issuer(device: TOTPDevice) -> str:
    configured = getattr(settings, "OTP_TOTP_ISSUER", None)
    if callable(configured):
        configured = configured(device)
    if isinstance(configured, str) and configured:
        return configured.replace(":", "")
    try:
        current_site = Site.objects.get_current()
    except Exception:
        return "Arthexis"
    return getattr(current_site, "name", "Arthexis") or "Arthexis"


def totp_provisioning_uri(device: TOTPDevice) -> str:
    username = str(device.user.get_username())
    issuer = get_totp_issuer(device)
    label = f"{issuer}:{username}" if issuer else username
    params = {
        "secret": totp_base32_key(device),
        "algorithm": "SHA1",
        "digits": device.digits,
        "period": device.step,
    }
    urlencoded_params = urlencode(params)
    if issuer:
        urlencoded_params += f"&issuer={quote(issuer)}"
    return f"otpauth://totp/{quote(label)}?{urlencoded_params}"


def render_totp_qr_data_uri(device: TOTPDevice) -> str:
    import qrcode

    image = qrcode.make(totp_provisioning_uri(device))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def generate_totp_name(user: models.Model) -> str:
    username = getattr(user, "get_username", lambda: None)()
    base_name = username or _("Authenticator")
    return str(base_name)[:64]


def verify_any_totp(
    user: models.Model,
    token: str,
    *,
    confirmed_only: bool = True,
) -> bool:
    devices: Iterable[TOTPDevice] = TOTPDevice.objects.filter(user=user)
    if confirmed_only:
        devices = devices.filter(confirmed=True)
    for device in devices:
        if device.verify_token(token):
            return True
    return False
