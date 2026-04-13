from __future__ import annotations

import base64
import time

import pytest
from django_otp.oath import TOTP

from apps.totp.models import TOTPDevice
from apps.totp.services import totp_base32_key
from apps.totp.services import totp_provisioning_uri
from apps.totp.services import verify_any_totp


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="alice", password="secret")


def _format_token(device: TOTPDevice, current_time: float) -> tuple[str, int]:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = current_time
    return f"{totp.token():0{device.digits}d}", totp.t()


@pytest.mark.django_db
def test_base32_key_encodes_hex_secret_without_padding(user):
    device = TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        key="3132333435363738393031323334353637383930",
    )

    expected_base32 = base64.b32encode(bytes.fromhex(device.key)).decode("ascii").rstrip("=")
    assert totp_base32_key(device) == expected_base32
    assert "=" not in totp_base32_key(device)


@pytest.mark.django_db
def test_provisioning_uri_uses_issuer_setting(user, settings):
    settings.OTP_TOTP_ISSUER = "Example:Issuer"
    device = TOTPDevice.objects.create(
        user=user,
        name="Primary",
        key="3132333435363738393031323334353637383930",
    )

    uri = totp_provisioning_uri(device)

    assert uri.startswith("otpauth://totp/ExampleIssuer%3Aalice?")
    assert f"secret={totp_base32_key(device)}" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
    assert "issuer=ExampleIssuer" in uri


@pytest.mark.django_db
def test_provisioning_uri_encodes_label_segments(user, settings):
    settings.OTP_TOTP_ISSUER = "Ops/Blue:Team"
    user.username = "alice/admin"
    user.save(update_fields=["username"])
    device = TOTPDevice.objects.create(
        user=user,
        name="Primary",
        key="3132333435363738393031323334353637383930",
    )

    uri = totp_provisioning_uri(device)

    assert uri.startswith("otpauth://totp/Ops%2FBlueTeam%3Aalice%2Fadmin?")
    assert "issuer=Ops%2FBlueTeam" in uri


@pytest.mark.django_db
def test_verify_token_updates_device_state(monkeypatch, user):
    device = TOTPDevice.objects.create(
        user=user,
        name="Phone",
        key="3132333435363738393031323334353637383930",
        confirmed=True,
        last_t=-1,
    )
    current_time = 1_700_000_000
    token, expected_t = _format_token(device, current_time)

    monkeypatch.setattr(time, "time", lambda: current_time)

    assert device.verify_token(token) is True

    device.refresh_from_db()
    assert device.last_t == expected_t
    assert device.throttling_failure_count == 0
    assert device.last_used_at is not None


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
