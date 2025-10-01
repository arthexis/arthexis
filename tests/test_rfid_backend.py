"""Tests for the RFID authentication backend."""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from core.backends import RFIDBackend
from core.models import EnergyAccount, RFID


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


def test_authenticate_returns_user_for_allowed_rfid(backend, user):
    account = EnergyAccount.objects.create(name="Test Account", user=user)
    rfid = RFID.objects.create(rfid="ABC123")
    account.rfids.add(rfid)

    authenticated = backend.authenticate(request=None, rfid="abc123")

    assert authenticated == user


def test_authenticate_returns_none_when_rfid_missing(backend):
    assert backend.authenticate(request=None, rfid=None) is None
    assert backend.authenticate(request=None, rfid="") is None


def test_authenticate_returns_none_when_rfid_not_allowed(backend, user):
    account = EnergyAccount.objects.create(name="Disallowed Account", user=user)
    rfid = RFID.objects.create(rfid="DEF456", allowed=False)
    account.rfids.add(rfid)

    assert backend.authenticate(request=None, rfid="def456") is None


def test_authenticate_returns_none_when_account_has_no_user(backend):
    account = EnergyAccount.objects.create(name="Unassigned Account")
    rfid = RFID.objects.create(rfid="FED654")
    account.rfids.add(rfid)

    assert backend.authenticate(request=None, rfid="fed654") is None


def test_get_user(backend, user):
    assert backend.get_user(user.pk) == user
    assert backend.get_user(999999) is None
