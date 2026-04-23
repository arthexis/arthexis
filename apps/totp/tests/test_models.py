from __future__ import annotations

import time

import pytest
from django_otp.oath import TOTP

from apps.totp.models import TOTPDevice
from apps.totp.services import verify_any_totp

@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="alice", password="secret")

def _format_token(device: TOTPDevice, current_time: float) -> tuple[str, int]:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = current_time
    return f"{totp.token():0{device.digits}d}", totp.t()

