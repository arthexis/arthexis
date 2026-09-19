from django.conf import settings
from django.db import models


class EnergyTariff(models.Model):
    code = models.SlugField(max_length=60, unique=True)
    price_mxn_per_kwh = models.DecimalField(max_digits=10, decimal_places=4)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.code


class CustomerAccount(models.Model):
    key = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    ocpp_id_tag = models.CharField(max_length=20, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="energy_accounts",
    )
    tariff = models.ForeignKey(
        EnergyTariff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accounts",
    )
    balance_kwh = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("key",)

    def __str__(self):
        return self.name


class LedgerEntry(models.Model):
    account = models.ForeignKey(
        CustomerAccount, on_delete=models.CASCADE, related_name="ledger_entries"
    )
    delta_kwh = models.DecimalField(max_digits=12, decimal_places=4)
    amount_mxn = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    source = models.CharField(max_length=40)
    external_reference = models.CharField(max_length=120, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at",)
