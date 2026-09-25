from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.energy.models import CustomerAccount, EnergyTariff, LedgerEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def account() -> CustomerAccount:
    return CustomerAccount.objects.create(
        key="account-1",
        name="Account",
    )


def test_tariff_string_uses_tariff_code() -> None:
    tariff = EnergyTariff.objects.create(
        code="standard",
        price_mxn_per_kwh=Decimal("4.2500"),
    )

    assert str(tariff) == "standard"


def test_tariff_code_is_unique() -> None:
    EnergyTariff.objects.create(
        code="standard",
        price_mxn_per_kwh=Decimal("4.2500"),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EnergyTariff.objects.create(
                code="standard",
                price_mxn_per_kwh=Decimal("5.0000"),
            )


def test_tariff_price_and_active_default_are_persisted() -> None:
    tariff = EnergyTariff.objects.create(
        code="standard",
        price_mxn_per_kwh=Decimal("4.2575"),
    )

    tariff.refresh_from_db()
    assert tariff.price_mxn_per_kwh == Decimal("4.2575")
    assert tariff.active


def test_account_string_uses_account_name() -> None:
    selected = CustomerAccount.objects.create(
        key="account-1",
        name="Main Account",
    )

    assert str(selected) == "Main Account"


def test_accounts_are_ordered_by_key() -> None:
    CustomerAccount.objects.create(key="z-account", name="Z")
    CustomerAccount.objects.create(key="a-account", name="A")

    assert list(
        CustomerAccount.objects.values_list("key", flat=True)
    ) == ["a-account", "z-account"]


def test_account_key_is_unique() -> None:
    CustomerAccount.objects.create(key="account-1", name="First")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CustomerAccount.objects.create(key="account-1", name="Second")


def test_account_defaults_describe_an_active_empty_account() -> None:
    selected = CustomerAccount.objects.create(
        key="account-1",
        name="Account",
    )

    selected.refresh_from_db()
    assert selected.ocpp_id_tag == ""
    assert selected.balance_kwh == Decimal("0.0000")
    assert selected.active
    assert selected.user is None
    assert selected.tariff is None


def test_account_user_and_tariff_relationships_are_optional_and_reversible() -> None:
    user = get_user_model().objects.create_user(
        username="energy-owner",
        password="unused",
    )
    tariff = EnergyTariff.objects.create(
        code="standard",
        price_mxn_per_kwh=Decimal("4.2500"),
    )

    selected = CustomerAccount.objects.create(
        key="account-1",
        name="Account",
        user=user,
        tariff=tariff,
    )

    assert selected.user == user
    assert selected.tariff == tariff
    assert user.energy_accounts.get() == selected
    assert tariff.accounts.get() == selected


def test_account_user_and_tariff_deletion_leave_the_account() -> None:
    user = get_user_model().objects.create_user(
        username="energy-owner",
        password="unused",
    )
    tariff = EnergyTariff.objects.create(
        code="standard",
        price_mxn_per_kwh=Decimal("4.2500"),
    )
    selected = CustomerAccount.objects.create(
        key="account-1",
        name="Account",
        user=user,
        tariff=tariff,
    )

    user.delete()
    tariff.delete()

    selected.refresh_from_db()
    assert selected.user is None
    assert selected.tariff is None


def test_ledger_entry_persists_energy_money_and_reference_values(
    account: CustomerAccount,
) -> None:
    entry = LedgerEntry.objects.create(
        account=account,
        delta_kwh=Decimal("-1.2345"),
        amount_mxn=Decimal("5.25"),
        source="charging",
        external_reference="session-1",
    )

    entry.refresh_from_db()
    assert entry.delta_kwh == Decimal("-1.2345")
    assert entry.amount_mxn == Decimal("5.25")
    assert entry.source == "charging"
    assert entry.external_reference == "session-1"
    assert account.ledger_entries.get() == entry


def test_ledger_optional_money_and_reference_fields_are_empty_by_default(
    account: CustomerAccount,
) -> None:
    entry = LedgerEntry.objects.create(
        account=account,
        delta_kwh=Decimal("10.0000"),
        source="purchase",
    )

    assert entry.amount_mxn is None
    assert entry.external_reference == ""


def test_ledger_entries_are_ordered_newest_first(
    account: CustomerAccount,
) -> None:
    older = LedgerEntry.objects.create(
        account=account,
        delta_kwh=Decimal("1.0000"),
        source="older",
    )
    LedgerEntry.objects.create(
        account=account,
        delta_kwh=Decimal("2.0000"),
        source="newer",
    )
    LedgerEntry.objects.filter(pk=older.pk).update(
        occurred_at=timezone.now() - timedelta(hours=1)
    )

    assert list(LedgerEntry.objects.values_list("source", flat=True)) == [
        "newer",
        "older",
    ]


def test_account_deletion_cascades_to_ledger_entries(
    account: CustomerAccount,
) -> None:
    entry = LedgerEntry.objects.create(
        account=account,
        delta_kwh=Decimal("1.0000"),
        source="test",
    )

    account.delete()

    assert not LedgerEntry.objects.filter(pk=entry.pk).exists()
