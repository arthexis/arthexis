"""Tests for the RFID authentication backend."""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from core.backends import RFIDBackend
from core.models import EnergyAccount, RFID
from ocpp.rfid.utils import convert_endianness_value


pytestmark = pytest.mark.django_db


@pytest.fixture
def backend():
    return RFIDBackend()


@pytest.fixture
def user():
    User = get_user_model()
    return User.objects.create_user(
        username=f"rfid-user-{uuid4()}",
        email="rfid@example.com",
        password="test-password",
    )


@pytest.fixture
def rfid_hex():
    def _factory():
        return uuid4().hex[:8].upper()

    return _factory


def test_authenticate_returns_user_for_allowed_rfid(backend, user, rfid_hex):
    account = EnergyAccount.objects.create(name="Test Account", user=user)
    rfid_value = rfid_hex()
    rfid = RFID.objects.create(rfid=rfid_value)
    account.rfids.add(rfid)

    authenticated = backend.authenticate(request=None, rfid=rfid_value.lower())

    assert authenticated == user


def test_authenticate_accepts_little_endian_value(backend, user, rfid_hex):
    account = EnergyAccount.objects.create(name="Little Account", user=user)
    base_value = rfid_hex()
    tag = RFID.objects.create(rfid=base_value, endianness=RFID.BIG_ENDIAN)
    account.rfids.add(tag)

    little_value = convert_endianness_value(
        base_value,
        from_endianness=RFID.BIG_ENDIAN,
        to_endianness=RFID.LITTLE_ENDIAN,
    )

    authenticated = backend.authenticate(request=None, rfid=little_value.lower())

    assert authenticated == user


def test_authenticate_accepts_big_endian_for_little_tag(backend, user, rfid_hex):
    account = EnergyAccount.objects.create(name="Little Stored Account", user=user)
    little_value = convert_endianness_value(
        rfid_hex(),
        from_endianness=RFID.BIG_ENDIAN,
        to_endianness=RFID.LITTLE_ENDIAN,
    )
    tag = RFID.objects.create(rfid=little_value, endianness=RFID.LITTLE_ENDIAN)
    account.rfids.add(tag)

    big_value = convert_endianness_value(
        little_value,
        from_endianness=RFID.LITTLE_ENDIAN,
        to_endianness=RFID.BIG_ENDIAN,
    )

    authenticated = backend.authenticate(request=None, rfid=big_value.lower())

    assert authenticated == user


def test_authenticate_returns_none_when_rfid_missing(backend):
    assert backend.authenticate(request=None, rfid=None) is None
    assert backend.authenticate(request=None, rfid="") is None


def test_authenticate_returns_none_when_rfid_not_allowed(backend, user, rfid_hex):
    account = EnergyAccount.objects.create(name="Disallowed Account", user=user)
    rfid = RFID.objects.create(rfid=rfid_hex(), allowed=False)
    account.rfids.add(rfid)

    assert backend.authenticate(request=None, rfid="def456") is None


def test_authenticate_returns_none_when_account_has_no_user(backend, rfid_hex):
    account = EnergyAccount.objects.create(name="Unassigned Account")
    rfid = RFID.objects.create(rfid=rfid_hex())
    account.rfids.add(rfid)

    assert backend.authenticate(request=None, rfid="fed654") is None


def test_get_user(backend, user):
    assert backend.get_user(user.pk) == user
    assert backend.get_user(999999) is None


def test_register_scan_updates_existing_to_little_endian(rfid_hex):
    base_value = rfid_hex()
    original = RFID.objects.create(rfid=base_value, endianness=RFID.BIG_ENDIAN)
    little_value = convert_endianness_value(
        base_value,
        from_endianness=RFID.BIG_ENDIAN,
        to_endianness=RFID.LITTLE_ENDIAN,
    )

    initial_count = RFID.objects.count()
    tag, created = RFID.register_scan(little_value, endianness=RFID.LITTLE_ENDIAN)

    assert not created
    assert tag.pk == original.pk
    original.refresh_from_db()
    assert original.rfid == little_value
    assert original.endianness == RFID.LITTLE_ENDIAN
    assert RFID.objects.count() == initial_count


def test_register_scan_updates_existing_to_big_endian(rfid_hex):
    base_value = rfid_hex()
    little_value = convert_endianness_value(
        base_value,
        from_endianness=RFID.BIG_ENDIAN,
        to_endianness=RFID.LITTLE_ENDIAN,
    )
    original = RFID.objects.create(rfid=little_value, endianness=RFID.LITTLE_ENDIAN)

    initial_count = RFID.objects.count()
    tag, created = RFID.register_scan(base_value, endianness=RFID.BIG_ENDIAN)

    assert not created
    assert tag.pk == original.pk
    original.refresh_from_db()
    assert original.rfid == base_value
    assert original.endianness == RFID.BIG_ENDIAN
    assert RFID.objects.count() == initial_count
