from django_otp.plugins.otp_totp.models import TOTPDevice as DjangoTOTPDevice, default_key


class TOTPDevice(DjangoTOTPDevice):
    """Local proxy for the django-otp TOTP device model."""

    class Meta:
        proxy = True
        app_label = "users"


__all__ = ["TOTPDevice", "default_key"]
