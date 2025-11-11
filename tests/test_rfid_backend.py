"""Tests for the RFID authentication backend."""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from core.backends import RFIDBackend
from core.models import CustomerAccount, RFID


pytestmark = [pytest.mark.django_db, pytest.mark.feature("rfid-scanner")]


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


def test_authenticate_returns_user_for_allowed_rfid(backend, user):
    account = CustomerAccount.objects.create(name="Test Account", user=user)
    rfid = RFID.objects.create(rfid="ABC123")
    account.rfids.add(rfid)

    authenticated = backend.authenticate(request=None, rfid="abc123")

    assert authenticated == user


def test_authenticate_returns_none_when_rfid_missing(backend):
    assert backend.authenticate(request=None, rfid=None) is None
    assert backend.authenticate(request=None, rfid="") is None


def test_authenticate_returns_none_when_rfid_not_allowed(backend, user):
    account = CustomerAccount.objects.create(name="Disallowed Account", user=user)
    rfid = RFID.objects.create(rfid="DEF456", allowed=False)
    account.rfids.add(rfid)

    assert backend.authenticate(request=None, rfid="def456") is None


def test_authenticate_returns_none_when_account_has_no_user(backend):
    account = CustomerAccount.objects.create(name="Unassigned Account")
    rfid = RFID.objects.create(rfid="FED654")
    account.rfids.add(rfid)

    assert backend.authenticate(request=None, rfid="fed654") is None


def test_authenticate_matches_by_prefix(backend, user):
    account = CustomerAccount.objects.create(name="Prefix Account", user=user)
    tag = RFID.objects.create(rfid="75075E74962580")
    account.rfids.add(tag)

    authenticated = backend.authenticate(request=None, rfid="75075E74")

    assert authenticated == user

    tag.refresh_from_db()
    assert tag.rfid == "75075E74"


def test_get_user(backend, user):
    assert backend.get_user(user.pk) == user
    assert backend.get_user(999999) is None


def test_register_scan_updates_existing_endianness():
    initial_count = RFID.objects.count()

    first_tag, created = RFID.register_scan("A1B2C3D4", endianness=RFID.BIG_ENDIAN)

    assert created is True
    assert RFID.objects.count() == initial_count + 1

    second_tag, created_again = RFID.register_scan(
        "D4C3B2A1", endianness=RFID.LITTLE_ENDIAN
    )

    assert created_again is False
    assert second_tag.pk == first_tag.pk
    assert RFID.objects.count() == initial_count + 1

    first_tag.refresh_from_db()
    assert first_tag.rfid == "D4C3B2A1"
    assert first_tag.endianness == RFID.LITTLE_ENDIAN

    third_tag, created_third = RFID.register_scan(
        "A1B2C3D4", endianness=RFID.BIG_ENDIAN
    )

    assert created_third is False
    assert third_tag.pk == first_tag.pk
    assert RFID.objects.count() == initial_count + 1

    first_tag.refresh_from_db()
    assert first_tag.rfid == "A1B2C3D4"
    assert first_tag.endianness == RFID.BIG_ENDIAN


def test_register_scan_prefers_shortest_identifier():
    RFID.objects.all().delete()

    tag, created = RFID.register_scan("75075E74962580", endianness=RFID.BIG_ENDIAN)
    assert created is True

    result, created_again = RFID.register_scan("75075E74", endianness=RFID.BIG_ENDIAN)

    assert created_again is False
    assert result.pk == tag.pk

    tag.refresh_from_db()
    assert tag.rfid == "75075E74"


def test_update_or_create_from_code_matches_prefix():
    tag = RFID.objects.create(rfid="CAFEBEEF123456", allowed=False, released=False)

    updated, created = RFID.update_or_create_from_code(
        "CAFEBEEF",
        {"allowed": True, "released": True},
    )

    assert created is False
    assert updated.pk == tag.pk

    tag.refresh_from_db()
    assert tag.rfid == "CAFEBEEF"
    assert tag.allowed is True
    assert tag.released is True
