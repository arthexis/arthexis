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


@pytest.mark.django_db

def test_verify_any_respects_confirmation(monkeypatch, user, settings):
    settings.OTP_TOTP_SYNC = False
    confirmed_device = TOTPDevice.objects.create(
        user=user,
        name="Confirmed",
        key="abcdefabcdefabcdefabcdefabcdefabcdefabcd",
        confirmed=True,
    )
    pending_device = TOTPDevice.objects.create(
        user=user,
        name="Pending",
        key="1234512345123451234512345123451234512345",
        confirmed=False,
    )
    confirmed_time = 1_700_000_100
    pending_time = confirmed_time + confirmed_device.step

    confirmed_token, _ = _format_token(confirmed_device, confirmed_time)
    pending_token, _ = _format_token(pending_device, pending_time)

    monkeypatch.setattr(time, "time", lambda: confirmed_time)
    assert verify_any_totp(user, confirmed_token, confirmed_only=True) is True

    monkeypatch.setattr(time, "time", lambda: pending_time)
    assert verify_any_totp(user, pending_token, confirmed_only=True) is False

    monkeypatch.setattr(time, "time", lambda: pending_time)
    assert verify_any_totp(user, pending_token, confirmed_only=False) is True

    pending_device.refresh_from_db()
    assert pending_device.last_used_at is not None
