import pytest
from django.core.management.base import CommandError

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.ocpp.auto_start_accounts import (
    RFID_FALLBACK_ACCOUNT_NAME,
    get_or_create_auto_start_account,
    get_or_create_rfid_fallback_account,
)


@pytest.mark.django_db
def test_auto_start_account_rejects_conflicting_rfid():
    RFID.objects.create(rfid="A1B2")

    with pytest.raises(CommandError, match="conflicts with an RFID"):
        get_or_create_auto_start_account("A1B2")


@pytest.mark.django_db
def test_auto_start_account_reuses_only_service_accounts():
    account, created = get_or_create_auto_start_account("TALLER")
    reused, reused_created = get_or_create_auto_start_account("TALLER")

    assert created is True
    assert reused_created is False
    assert reused == account
    account.service_account = False
    account.save(update_fields=["service_account"])
    with pytest.raises(CommandError, match="belongs to a non-service account"):
        get_or_create_auto_start_account("TALLER")


@pytest.mark.django_db
def test_rfid_fallback_account_requires_service_account():
    CustomerAccount.objects.create(name=RFID_FALLBACK_ACCOUNT_NAME, service_account=False)

    with pytest.raises(CommandError, match="fallback account is not a service account"):
        get_or_create_rfid_fallback_account()
