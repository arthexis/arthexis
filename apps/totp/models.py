"""Compatibility exports for the shared django-otp TOTP device model."""

from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.util import hex_validator
from django_otp.util import random_hex


def default_key() -> str:
    return random_hex(20)


def key_validator(value: str):
    return hex_validator()(value)


__all__ = ["TOTPDevice", "default_key", "key_validator"]
