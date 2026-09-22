from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.energy.models import CustomerAccount, EnergyTariff, LedgerEntry


class EnergyTariffTests(TestCase):
    def test_string_uses_tariff_code(self) -> None:
        tariff = EnergyTariff.objects.create(
            code="standard",
            price_mxn_per_kwh=Decimal("4.2500"),
        )

        self.assertEqual(str(tariff), "standard")

    def test_code_is_unique(self) -> None:
        EnergyTariff.objects.create(
            code="standard",
            price_mxn_per_kwh=Decimal("4.2500"),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EnergyTariff.objects.create(
                    code="standard",
                    price_mxn_per_kwh=Decimal("5.0000"),
                )

    def test_price_and_active_default_are_persisted(self) -> None:
        tariff = EnergyTariff.objects.create(
            code="standard",
            price_mxn_per_kwh=Decimal("4.2575"),
        )

        tariff.refresh_from_db()
        self.assertEqual(tariff.price_mxn_per_kwh, Decimal("4.2575"))
        self.assertTrue(tariff.active)


class CustomerAccountTests(TestCase):
    def test_string_uses_account_name(self) -> None:
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Main Account",
        )

        self.assertEqual(str(account), "Main Account")

    def test_accounts_are_ordered_by_key(self) -> None:
        CustomerAccount.objects.create(key="z-account", name="Z")
        CustomerAccount.objects.create(key="a-account", name="A")

        self.assertEqual(
            list(CustomerAccount.objects.values_list("key", flat=True)),
            ["a-account", "z-account"],
        )

    def test_key_is_unique(self) -> None:
        CustomerAccount.objects.create(key="account-1", name="First")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomerAccount.objects.create(key="account-1", name="Second")

    def test_defaults_describe_an_active_empty_account(self) -> None:
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
        )

        account.refresh_from_db()
        self.assertEqual(account.ocpp_id_tag, "")
        self.assertEqual(account.balance_kwh, Decimal("0.0000"))
        self.assertTrue(account.active)
        self.assertIsNone(account.user)
        self.assertIsNone(account.tariff)

    def test_user_and_tariff_relationships_are_optional_and_reversible(self) -> None:
        user = get_user_model().objects.create_user(
            username="energy-owner",
            password="unused",
        )
        tariff = EnergyTariff.objects.create(
            code="standard",
            price_mxn_per_kwh=Decimal("4.2500"),
        )

        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
            user=user,
            tariff=tariff,
        )

        self.assertEqual(account.user, user)
        self.assertEqual(account.tariff, tariff)
        self.assertEqual(user.energy_accounts.get(), account)
        self.assertEqual(tariff.accounts.get(), account)

    def test_user_and_tariff_deletion_leave_the_account(self) -> None:
        user = get_user_model().objects.create_user(
            username="energy-owner",
            password="unused",
        )
        tariff = EnergyTariff.objects.create(
            code="standard",
            price_mxn_per_kwh=Decimal("4.2500"),
        )
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
            user=user,
            tariff=tariff,
        )

        user.delete()
        tariff.delete()

        account.refresh_from_db()
        self.assertIsNone(account.user)
        self.assertIsNone(account.tariff)


class LedgerEntryTests(TestCase):
    def setUp(self) -> None:
        self.account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
        )

    def test_entry_persists_energy_money_and_reference_values(self) -> None:
        entry = LedgerEntry.objects.create(
            account=self.account,
            delta_kwh=Decimal("-1.2345"),
            amount_mxn=Decimal("5.25"),
            source="charging",
            external_reference="session-1",
        )

        entry.refresh_from_db()
        self.assertEqual(entry.delta_kwh, Decimal("-1.2345"))
        self.assertEqual(entry.amount_mxn, Decimal("5.25"))
        self.assertEqual(entry.source, "charging")
        self.assertEqual(entry.external_reference, "session-1")
        self.assertEqual(self.account.ledger_entries.get(), entry)

    def test_optional_money_and_reference_fields_are_empty_by_default(self) -> None:
        entry = LedgerEntry.objects.create(
            account=self.account,
            delta_kwh=Decimal("10.0000"),
            source="purchase",
        )

        self.assertIsNone(entry.amount_mxn)
        self.assertEqual(entry.external_reference, "")

    def test_entries_are_ordered_newest_first(self) -> None:
        older = LedgerEntry.objects.create(
            account=self.account,
            delta_kwh=Decimal("1.0000"),
            source="older",
        )
        newer = LedgerEntry.objects.create(
            account=self.account,
            delta_kwh=Decimal("2.0000"),
            source="newer",
        )
        LedgerEntry.objects.filter(pk=older.pk).update(
            occurred_at=timezone.now() - timedelta(hours=1)
        )

        self.assertEqual(
            list(LedgerEntry.objects.values_list("source", flat=True)),
            ["newer", "older"],
        )

    def test_account_deletion_cascades_to_ledger_entries(self) -> None:
        entry = LedgerEntry.objects.create(
            account=self.account,
            delta_kwh=Decimal("1.0000"),
            source="test",
        )

        self.account.delete()

        self.assertFalse(LedgerEntry.objects.filter(pk=entry.pk).exists())
