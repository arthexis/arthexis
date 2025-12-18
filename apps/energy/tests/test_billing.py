from __future__ import annotations

from datetime import time
from decimal import Decimal

import pytest

from apps.energy.models import CustomerAccount, EnergyTariff


@pytest.mark.django_db
def test_customer_account_authorization_with_tariff_and_balance():
    tariff = EnergyTariff(
        year=2024,
        season=EnergyTariff.Season.ANNUAL,
        zone=EnergyTariff.Zone.ONE,
        contract_type=EnergyTariff.ContractType.DOMESTIC,
        period=EnergyTariff.Period.FLAT,
        unit=EnergyTariff.Unit.KWH,
        start_time=time(0, 0),
        end_time=time(1, 0),
        price_mxn=Decimal("5"),
        cost_mxn=Decimal("1"),
    )
    tariff.save()
    account = CustomerAccount.objects.create(
        name="DEMO",
        balance_mxn=Decimal("10"),
        energy_tariff=tariff,
    )

    assert account.potential_purchase_kw == Decimal("2")
    assert account.can_authorize() is True


def test_customer_account_service_account_override():
    account = CustomerAccount(name="SERVICE")
    account.service_account = True
    account.balance_mxn = Decimal("0")
    account.energy_tariff = None

    assert account.can_authorize() is True
