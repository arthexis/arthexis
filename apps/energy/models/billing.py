from __future__ import annotations

from datetime import timedelta, time as datetime_time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager


class EnergyTariffManager(EntityManager):
    def get_by_natural_key(
        self,
        year: int,
        season: str,
        zone: str,
        contract_type: str,
        period: str,
        unit: str,
        start_time,
        end_time,
    ):
        if isinstance(start_time, str):
            start_time = datetime_time.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = datetime_time.fromisoformat(end_time)
        return self.get(
            year=year,
            season=season,
            zone=zone,
            contract_type=contract_type,
            period=period,
            unit=unit,
            start_time=start_time,
            end_time=end_time,
        )


class EnergyTariff(Entity):
    class Zone(models.TextChoices):
        ONE = "1", _("Zone 1")
        ONE_A = "1A", _("Zone 1A")
        ONE_B = "1B", _("Zone 1B")
        ONE_C = "1C", _("Zone 1C")
        ONE_D = "1D", _("Zone 1D")
        ONE_E = "1E", _("Zone 1E")
        ONE_F = "1F", _("Zone 1F")

    class Season(models.TextChoices):
        ANNUAL = "annual", _("All year")
        SUMMER = "summer", _("Summer season")
        NON_SUMMER = "non_summer", _("Non-summer season")

    class Period(models.TextChoices):
        FLAT = "flat", _("Flat rate")
        BASIC = "basic", _("Basic block")
        INTERMEDIATE_1 = "intermediate_1", _("Intermediate block 1")
        INTERMEDIATE_2 = "intermediate_2", _("Intermediate block 2")
        EXCESS = "excess", _("Excess consumption")
        BASE = "base", _("Base")
        INTERMEDIATE = "intermediate", _("Intermediate")
        PEAK = "peak", _("Peak")
        CRITICAL_PEAK = "critical_peak", _("Critical peak")
        DEMAND = "demand", _("Demand charge")
        CAPACITY = "capacity", _("Capacity charge")
        DISTRIBUTION = "distribution", _("Distribution charge")
        FIXED = "fixed", _("Fixed charge")

    class ContractType(models.TextChoices):
        DOMESTIC = "domestic", _("Domestic service (Tarifa 1)")
        DAC = "dac", _("High consumption domestic (DAC)")
        PDBT = "pdbt", _("General service low demand (PDBT)")
        GDBT = "gdbt", _("General service high demand (GDBT)")
        GDMTO = "gdmto", _("General distribution medium tension (GDMTO)")
        GDMTH = "gdmth", _("General distribution medium tension hourly (GDMTH)")

    class Unit(models.TextChoices):
        KWH = "kwh", _("Kilowatt-hour")
        KW = "kw", _("Kilowatt")
        MONTH = "month", _("Monthly charge")

    year = models.PositiveIntegerField(
        validators=[MinValueValidator(2000)],
        help_text=_("Calendar year when the tariff applies."),
    )
    season = models.CharField(
        max_length=16,
        choices=Season.choices,
        default=Season.ANNUAL,
        help_text=_("Season or applicability window defined by CFE."),
    )
    zone = models.CharField(
        max_length=3,
        choices=Zone.choices,
        help_text=_("CFE climate zone associated with the tariff."),
    )
    contract_type = models.CharField(
        max_length=16,
        choices=ContractType.choices,
        help_text=_("Type of service contract regulated by CFE."),
    )
    period = models.CharField(
        max_length=32,
        choices=Period.choices,
        help_text=_("Tariff block, demand component, or time-of-use period."),
    )
    unit = models.CharField(
        max_length=16,
        choices=Unit.choices,
        default=Unit.KWH,
        help_text=_("Measurement unit for the tariff charge."),
    )
    start_time = models.TimeField(
        help_text=_("Start time for the tariff's applicability window."),
    )
    end_time = models.TimeField(
        help_text=_("End time for the tariff's applicability window."),
    )
    price_mxn = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text=_("Customer price per unit in MXN."),
    )
    cost_mxn = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text=_("Provider cost per unit in MXN."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text=_("Context or special billing conditions published by CFE."),
    )

    objects = EnergyTariffManager()

    class Meta:
        verbose_name = _("Energy Tariff")
        verbose_name_plural = _("Energy Tariffs")
        db_table = "core_energytariff"
        ordering = (
            "-year",
            "season",
            "zone",
            "contract_type",
            "period",
            "start_time",
        )
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "year",
                    "season",
                    "zone",
                    "contract_type",
                    "period",
                    "unit",
                    "start_time",
                    "end_time",
                ],
                name="uniq_energy_tariff_schedule",
            )
        ]
        indexes = [
            models.Index(
                fields=["year", "season", "zone", "contract_type"],
                name="energy_tariff_scope_idx",
            )
        ]

    def clean(self):
        super().clean()
        if self.start_time >= self.end_time:
            raise ValidationError(
                {"end_time": _("End time must be after the start time.")}
            )

    def __str__(self):  # pragma: no cover - simple representation
        return _("%(contract)s %(zone)s %(season)s %(year)s (%(period)s)") % {
            "contract": self.get_contract_type_display(),
            "zone": self.zone,
            "season": self.get_season_display(),
            "year": self.year,
            "period": self.get_period_display(),
        }

    def natural_key(self):  # pragma: no cover - simple representation
        return (
            self.year,
            self.season,
            self.zone,
            self.contract_type,
            self.period,
            self.unit,
            self.start_time.isoformat(),
            self.end_time.isoformat(),
        )

    natural_key.dependencies = []  # type: ignore[attr-defined]


from apps.maps import models as map_models


class Location(map_models.Location):
    class Meta(map_models.Location.Meta):
        proxy = True
        app_label = "energy"


class CustomerAccount(Entity):
    """Track kW energy credits, balance, and billing for a user."""

    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="customer_account",
        null=True,
        blank=True,
    )
    odoo_customer = models.JSONField(
        null=True,
        blank=True,
        help_text="Selected customer from Odoo (id, name, and contact details)",
    )
    rfids = models.ManyToManyField(
        "cards.RFID",
        blank=True,
        related_name="energy_accounts",
        db_table="core_account_rfids",
        verbose_name="RFIDs",
    )
    service_account = models.BooleanField(
        default=False,
        help_text="Allow transactions even when the balance is zero or negative",
    )
    balance_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Available currency balance for auto top-ups.",
    )
    minimum_purchase_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Default amount to purchase when topping up via credit card.",
    )
    energy_tariff = models.ForeignKey(
        "EnergyTariff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounts",
        help_text="Tariff used to convert currency balance to energy credits.",
    )
    credit_card_brand = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Brand of the backup credit card.",
    )
    credit_card_last4 = models.CharField(
        max_length=4,
        blank=True,
        default="",
        help_text="Last four digits of the backup credit card.",
    )
    credit_card_exp_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Expiration month for the backup credit card.",
    )
    credit_card_exp_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Expiration year for the backup credit card.",
    )
    live_subscription_product = models.ForeignKey(
        "odoo.OdooProduct",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="live_subscription_accounts",
    )
    live_subscription_start_date = models.DateField(null=True, blank=True)
    live_subscription_next_renewal = models.DateField(null=True, blank=True)

    def can_authorize(self) -> bool:
        """Return True if this account should be authorized for charging."""
        if self.service_account:
            return True
        if self.balance_kw > 0:
            return True
        potential = self.potential_purchase_kw
        return potential > 0

    @property
    def credits_kw(self):
        """Total kW credits recorded in the energy ledger."""
        from django.db.models import Sum
        from decimal import Decimal
        from .transactions import EnergyTransaction

        total = self.energy_transactions.filter(
            direction=EnergyTransaction.Direction.CREDIT
        ).aggregate(total=Sum("delta_kw"))["total"]
        return total if total is not None else Decimal("0")

    @property
    def total_kw_spent(self):
        """Total kW debited from the account via the energy ledger."""
        from django.db.models import Sum
        from decimal import Decimal
        from .transactions import EnergyTransaction

        total = self.energy_transactions.filter(
            direction=EnergyTransaction.Direction.DEBIT
        ).aggregate(total=Sum("delta_kw"))["total"]
        if total is None:
            return Decimal("0")
        return Decimal("0") - Decimal(str(total))

    @property
    def balance_kw(self):
        """Remaining kW available for the customer account."""
        from django.db.models import Sum
        from decimal import Decimal

        total = self.energy_transactions.aggregate(total=Sum("delta_kw"))["total"]
        return total if total is not None else Decimal("0")

    @property
    def potential_purchase_kw(self):
        """kW that could be purchased using the current balance and tariff."""
        if not self.energy_tariff:
            return Decimal("0")
        price = self.energy_tariff.price_mxn
        if price is None or price <= 0:
            return Decimal("0")
        if self.balance_mxn <= 0:
            return Decimal("0")
        return self.balance_mxn / price

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.upper()
        if self.live_subscription_product and not self.live_subscription_start_date:
            self.live_subscription_start_date = timezone.now().date()
        if (
            self.live_subscription_product
            and self.live_subscription_start_date
            and not self.live_subscription_next_renewal
        ):
            self.live_subscription_next_renewal = (
                self.live_subscription_start_date
                + timedelta(days=self.live_subscription_product.renewal_period)
            )
        super().save(*args, **kwargs)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = "Customer Account"
        verbose_name_plural = "Customer Accounts"
        db_table = "core_account"


@receiver(m2m_changed, sender=CustomerAccount.rfids.through)
def _rfid_unique_customer_account(
    sender, instance, action, reverse, model, pk_set, **kwargs
):
    """Prevent associating an RFID with more than one customer account."""

    if action == "pre_add":
        if reverse:  # adding customer accounts to an RFID
            if instance.energy_accounts.exclude(pk__in=pk_set).exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one customer account."
                )
        else:  # adding RFIDs to a customer account
            conflict = model.objects.filter(
                pk__in=pk_set, energy_accounts__isnull=False
            ).exclude(energy_accounts=instance)
            if conflict.exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one customer account."
                )
