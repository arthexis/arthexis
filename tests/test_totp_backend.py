import uuid
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.backends import TOTPBackend, TOTP_DEVICE_NAME


pytestmark = pytest.mark.django_db


def create_staff_user_with_device(*, confirmed=True, name=TOTP_DEVICE_NAME, is_active=True):
    User = get_user_model()
    unique_suffix = uuid.uuid4().hex
    user = User.objects.create_user(
        username=f"staff-{unique_suffix}",
        email=f"staff-{unique_suffix}@example.com",
        password="password123",
        is_staff=True,
        is_active=is_active,
    )
    device = TOTPDevice.objects.create(
        user=user,
        name=name,
        confirmed=confirmed,
    )
    return user, device


def _current_token(device):
    totp = TOTP(
        key=device.bin_key,
        step=device.step,
        t0=device.t0,
        digits=device.digits,
    )
    return format(totp.token(), f"0{device.digits}d")


def test_authenticate_valid_token():
    user, device = create_staff_user_with_device()
    token = _current_token(device)

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is not None
    assert result.pk == user.pk
    assert getattr(result, "otp_device") == device


@pytest.mark.parametrize("otp_value", [None, "", "   "])
def test_authenticate_rejects_blank_tokens(otp_value):
    user, _ = create_staff_user_with_device()

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=otp_value)

    assert result is None


def test_authenticate_inactive_user_returns_none():
    user, device = create_staff_user_with_device(is_active=False)
    token = _current_token(device)

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is None


def test_authenticate_requires_confirmed_device():
    user, device = create_staff_user_with_device(confirmed=False)
    token = _current_token(device)

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is None


def test_authenticate_falls_back_to_any_confirmed_device():
    user, device = create_staff_user_with_device(name="unexpected")
    token = _current_token(device)

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is not None
    assert result.pk == user.pk
    assert getattr(result, "otp_device") == device


def test_authenticate_prefers_named_device():
    user, _fallback_device = create_staff_user_with_device(name="unexpected")
    named_device = TOTPDevice.objects.create(
        user=user,
        name=TOTP_DEVICE_NAME,
        confirmed=True,
    )
    token = _current_token(named_device)

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is not None
    assert result.pk == user.pk
    assert getattr(result, "otp_device") == named_device


def test_authenticate_handles_verify_exceptions(monkeypatch):
    user, device = create_staff_user_with_device()
    token = _current_token(device)

    monkeypatch.setattr(TOTPDevice, "verify_token", Mock(side_effect=Exception("boom")))

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is None


def test_authenticate_returns_none_when_verification_fails(monkeypatch):
    user, device = create_staff_user_with_device()
    token = _current_token(device)

    monkeypatch.setattr(TOTPDevice, "verify_token", lambda *args, **kwargs: False)

    backend = TOTPBackend()
    result = backend.authenticate(None, username=user.username, otp_token=token)

    assert result is None


def test_get_user_returns_user_and_none_for_unknown():
    user, _ = create_staff_user_with_device()

    backend = TOTPBackend()

    assert backend.get_user(user.pk) == user
    assert backend.get_user(user.pk + 1) is None
