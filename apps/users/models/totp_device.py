import time
from base64 import b32encode
from binascii import unhexlify
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import models

from django_otp.models import Device, ThrottlingMixin, TimestampMixin
from django_otp.oath import TOTP
from django_otp.util import hex_validator, random_hex


def default_key():
    return random_hex(20)


def key_validator(value):
    return hex_validator()(value)


class TOTPDevice(TimestampMixin, ThrottlingMixin, Device):
    """
    A TOTP :class:`~django_otp.models.Device` stored under the users app.

    This is adapted from ``django_otp.plugins.otp_totp.models.TOTPDevice`` but
    lives in the ``users`` domain to avoid the ``otp_totp`` app label.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        help_text="The user that this device belongs to.",
        on_delete=models.CASCADE,
        related_name="user_totp_devices",
    )
    key = models.CharField(
        max_length=80,
        validators=[key_validator],
        default=default_key,
        help_text="A hex-encoded secret key of up to 40 bytes.",
    )
    step = models.PositiveSmallIntegerField(
        default=30, help_text="The time step in seconds."
    )
    t0 = models.BigIntegerField(
        default=0, help_text="The Unix time at which to begin counting steps."
    )
    digits = models.PositiveSmallIntegerField(
        choices=[(6, 6), (8, 8)],
        default=6,
        help_text="The number of digits to expect in a token.",
    )
    tolerance = models.PositiveSmallIntegerField(
        default=1, help_text="The number of time steps in the past or future to allow."
    )
    drift = models.SmallIntegerField(
        default=0,
        help_text="The number of time steps the prover is known to deviate from our clock.",
    )
    last_t = models.BigIntegerField(
        default=-1,
        help_text="The t value of the latest verified token. The next token must be at a higher time step.",
    )

    class Meta(Device.Meta):
        verbose_name = "TOTP device"
        db_table = "otp_totp_totpdevice"

    @property
    def bin_key(self):
        """The secret key as a binary string."""

        return unhexlify(self.key.encode())

    def verify_token(self, token):
        OTP_TOTP_SYNC = getattr(settings, "OTP_TOTP_SYNC", True)

        verify_allowed, _ = self.verify_is_allowed()
        if not verify_allowed:
            return False

        try:
            token = int(token)
        except Exception:
            verified = False
        else:
            key = self.bin_key

            totp = TOTP(key, self.step, self.t0, self.digits, self.drift)
            totp.time = time.time()

            verified = totp.verify(token, self.tolerance, self.last_t + 1)
            if verified:
                self.last_t = totp.t()
                if OTP_TOTP_SYNC:
                    self.drift = totp.drift
                self.throttle_reset(commit=False)
                self.set_last_used_timestamp(commit=False)
                self.save()

        if not verified:
            self.throttle_increment(commit=True)

        return verified

    def get_throttle_factor(self):
        return getattr(settings, "OTP_TOTP_THROTTLE_FACTOR", 1)

    @property
    def config_url(self):
        """A URL for configuring Google Authenticator or similar."""

        label = str(self.user.get_username())
        params = {
            "secret": b32encode(self.bin_key),
            "algorithm": "SHA1",
            "digits": self.digits,
            "period": self.step,
        }
        urlencoded_params = urlencode(params)

        issuer = self._read_str_from_settings("OTP_TOTP_ISSUER")
        if issuer:
            issuer = issuer.replace(":", "")
            label = f"{issuer}:{label}"
            urlencoded_params += f"&issuer={quote(issuer)}"

        image = self._read_str_from_settings("OTP_TOTP_IMAGE")
        if image:
            urlencoded_params += f"&image={quote(image, safe=':/')}"

        url = f"otpauth://totp/{quote(label)}?{urlencoded_params}"

        return url

    def _read_str_from_settings(self, key):
        val = getattr(settings, key, None)
        if callable(val):
            val = val(self)
        if isinstance(val, str) and val != "":
            return val
        return None



class TOTPDevice(OTPTOTPDevice):
    """Local proxy for the django-otp TOTP device model."""

    class Meta:
        proxy = True
        app_label = "users"


__all__ = ["TOTPDevice", "default_key"]
