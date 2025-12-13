"""TOTP device model used for one-time password logins."""

from __future__ import annotations

import base64
import io
import time
from binascii import unhexlify
from typing import Iterable
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import models
from django.utils.translation import gettext_lazy as _

from django_otp.models import Device, ThrottlingMixin, TimestampMixin
from django_otp.oath import TOTP
from django_otp.util import hex_validator, random_hex


def default_key() -> str:
    return random_hex(20)


def key_validator(value: str):
    return hex_validator()(value)


class TOTPDevice(TimestampMixin, ThrottlingMixin, Device):
    """
    Time-based one-time password device bound to a specific user.

    The fields mirror :func:`django_otp.oath.totp` arguments and keep enough
    state to prevent token replay while allowing small clock drift.
    """

    key = models.CharField(
        max_length=80,
        validators=[key_validator],
        default=default_key,
        help_text=_("A hex-encoded secret key of up to 40 bytes."),
    )
    step = models.PositiveSmallIntegerField(
        default=30, help_text=_("The time step in seconds."),
    )
    t0 = models.BigIntegerField(
        default=0, help_text=_("The Unix time at which to begin counting steps."),
    )
    digits = models.PositiveSmallIntegerField(
        choices=[(6, 6), (8, 8)],
        default=6,
        help_text=_("The number of digits to expect in a token."),
    )
    tolerance = models.PositiveSmallIntegerField(
        default=1, help_text=_("The number of time steps in the past or future to allow."),
    )
    drift = models.SmallIntegerField(
        default=0,
        help_text=_("The number of time steps the prover is known to deviate from our clock."),
    )
    last_t = models.BigIntegerField(
        default=-1,
        help_text=_("The t value of the latest verified token. The next token must be at a higher time step."),
    )
    confirmed = models.BooleanField(
        default=False,
        help_text=_("Is this device ready for use?"),
    )

    class Meta(Device.Meta):
        verbose_name = _("TOTP device")
        verbose_name_plural = _("TOTP devices")
        ordering = ("user", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "name"), name="totp_device_unique_name_per_user"
            )
        ]

    @property
    def bin_key(self) -> bytes:
        """Return the secret key as raw bytes."""

        return unhexlify(self.key.encode())

    @property
    def base32_key(self) -> str:
        """Return a base32 encoded secret suitable for authenticator apps."""

        return base64.b32encode(self.bin_key).decode("utf-8").strip("=")

    def verify_token(self, token: str) -> bool:
        otp_sync = getattr(settings, "OTP_TOTP_SYNC", True)

        verify_allowed, _ = self.verify_is_allowed()
        if not verify_allowed:
            return False

        verified = False
        try:
            token_int = int(str(token).strip())
        except Exception:
            verified = False
        else:
            totp = TOTP(self.bin_key, self.step, self.t0, self.digits, self.drift)
            totp.time = time.time()

            verified = totp.verify(token_int, self.tolerance, self.last_t + 1)
            if verified:
                self.last_t = totp.t()
                if otp_sync:
                    self.drift = totp.drift
                self.throttle_reset(commit=False)
                self.set_last_used_timestamp(commit=False)
                self.save()

        if not verified:
            self.throttle_increment(commit=True)

        return verified

    def get_throttle_factor(self):
        return getattr(settings, "OTP_TOTP_THROTTLE_FACTOR", 1)

    def provisioning_uri(self) -> str:
        """Return an otpauth URI for QR-code provisioning."""

        username = str(self.user.get_username())
        issuer = self._issuer()
        label = f"{issuer}:{username}" if issuer else username
        params = {
            "secret": self.base32_key,
            "algorithm": "SHA1",
            "digits": self.digits,
            "period": self.step,
        }
        urlencoded_params = urlencode(params)
        if issuer:
            urlencoded_params += f"&issuer={quote(issuer)}"
        return f"otpauth://totp/{quote(label)}?{urlencoded_params}"

    def _issuer(self) -> str:
        configured = getattr(settings, "OTP_TOTP_ISSUER", None)
        if callable(configured):
            configured = configured(self)
        if isinstance(configured, str) and configured:
            return configured.replace(":", "")
        try:
            current_site = Site.objects.get_current()
        except Exception:
            return "Arthexis"
        return getattr(current_site, "name", "Arthexis") or "Arthexis"

    @classmethod
    def generate_key(cls) -> str:
        return default_key()

    @classmethod
    def generate_name(cls, user: models.Model) -> str:
        username = getattr(user, "get_username", lambda: None)()
        base_name = username or _("Authenticator")
        return str(base_name)[:64]

    def render_qr_data_uri(self) -> str:
        """Return a data URI for the provisioning QR code."""

        import qrcode

        image = qrcode.make(self.provisioning_uri())
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @classmethod
    def verify_any(cls, user: models.Model, token: str, *, confirmed_only: bool = True) -> bool:
        """Return ``True`` when ``token`` verifies against any user device."""

        devices: Iterable[TOTPDevice] = cls.objects.filter(user=user)
        if confirmed_only:
            devices = devices.filter(confirmed=True)
        for device in devices:
            if device.verify_token(token):
                return True
        return False

    def __str__(self):
        return f"{self.name} ({self.user})"
