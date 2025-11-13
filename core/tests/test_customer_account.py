"""Tests for the CustomerAccount energy credit helpers."""

from decimal import Decimal

from django.test import TestCase

from core.models import CustomerAccount, EnergyCredit


class CustomerAccountCreditsTests(TestCase):
    """Validate the CustomerAccount.credits_kw property."""

    def test_credits_kw_defaults_to_zero(self):
        account = CustomerAccount.objects.create(name="Sample Account")

        self.assertEqual(account.credits_kw, Decimal("0"))

    def test_credits_kw_sums_related_energy_credits(self):
        account = CustomerAccount.objects.create(name="Sample Account 2")
        EnergyCredit.objects.create(account=account, amount_kw=Decimal("1.25"))
        EnergyCredit.objects.create(account=account, amount_kw=Decimal("3.75"))

        self.assertEqual(account.credits_kw, Decimal("5.00"))
