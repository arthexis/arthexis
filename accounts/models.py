from __future__ import annotations

import json
import logging
from datetime import date as datetime_date, datetime as datetime_datetime, time as datetime_time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import DatabaseError, models, transaction
from django.db.models import ExpressionWrapper, FloatField, F, Q, Sum
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import formats, timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext, gettext_lazy as _, override

from apps.core.celery_utils import normalize_periodic_task_name
from apps.core.entity import Entity, EntityManager
from apps.core.language import (
    default_report_language,
    normalize_report_language,
    normalize_report_title,
)


logger = logging.getLogger(__name__)


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


class Location(Entity):
    """Physical location available for business operations."""

    name = models.CharField(max_length=200)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    zone = models.CharField(
        max_length=3,
        choices=EnergyTariff.Zone.choices,
        blank=True,
        help_text=_("CFE climate zone used to select matching energy tariffs."),
    )
    contract_type = models.CharField(
        max_length=16,
        choices=EnergyTariff.ContractType.choices,
        blank=True,
        help_text=_("CFE service contract type required to match energy tariff pricing."),
    )
    address_line1 = models.CharField(
        _("Street address"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Primary street address or location description."),
    )
    address_line2 = models.CharField(
        _("Street address line 2"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Additional address information such as suite or building."),
    )
    city = models.CharField(
        _("City"),
        max_length=128,
        blank=True,
        default="",
    )
    state = models.CharField(
        _("State / Province"),
        max_length=128,
        blank=True,
        default="",
    )
    postal_code = models.CharField(
        _("Postal code"),
        max_length=32,
        blank=True,
        default="",
    )
    country = models.CharField(
        _("Country"),
        max_length=64,
        blank=True,
        default="",
    )
    phone_number = models.CharField(
        _("Phone number"),
        max_length=32,
        blank=True,
        default="",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_locations",
        verbose_name=_("Assigned to"),
        help_text=_("Optional user responsible for this location."),
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")
        db_table = "core_location"


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
        "core.RFID",
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
        "core.Product",
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
        """Total kW energy credits added to the customer account."""
        from django.db.models import Sum
        from decimal import Decimal

        total = self.credits.aggregate(total=Sum("amount_kw"))["total"]
        return total if total is not None else Decimal("0")

    @property
    def total_kw_spent(self):
        """Total kW consumed across all transactions."""
        from django.db.models import F, Sum, ExpressionWrapper, FloatField
        from decimal import Decimal

        expr = ExpressionWrapper(
            F("meter_stop") - F("meter_start"), output_field=FloatField()
        )
        total = self.transactions.filter(
            meter_start__isnull=False, meter_stop__isnull=False
        ).aggregate(total=Sum(expr))["total"]
        if total is None:
            return Decimal("0")
        return Decimal(str(total))

    @property
    def balance_kw(self):
        """Remaining kW available for the customer account."""
        return self.credits_kw - self.total_kw_spent

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


# Ensure each RFID can only be linked to one customer account
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


class EnergyCredit(Entity):
    """Energy credits added to a customer account."""

    account = models.ForeignKey(
        CustomerAccount, on_delete=models.CASCADE, related_name="credits"
    )
    amount_kw = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Energy (kW)"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_entries",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        user = (
            self.account.user
            if self.account.user
            else f"Customer Account {self.account_id}"
        )
        return f"{self.amount_kw} kW for {user}"

    class Meta:
        verbose_name = "Energy Credit"
        verbose_name_plural = "Energy Credits"
        db_table = "core_credit"


class EnergyTransaction(Entity):
    """Record of currency-to-energy purchases for an account."""

    account = models.ForeignKey(
        CustomerAccount, on_delete=models.CASCADE, related_name="energy_transactions"
    )
    tariff = models.ForeignKey(
        "EnergyTariff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="energy_transactions",
        help_text="Tariff in effect when the purchase occurred.",
    )
    purchased_kw = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Number of kW purchased for the account.",
    )
    charged_amount_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Currency amount used for the purchase.",
    )
    conversion_factor = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Conversion factor (kW per MXN) applied at purchase time.",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Energy Transaction"
        verbose_name_plural = "Energy Transactions"
        ordering = ("-created_on",)
        db_table = "core_energytransaction"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.purchased_kw} kW on {self.created_on:%Y-%m-%d}"


class ClientReportSchedule(Entity):
    """Configuration for recurring :class:`ClientReport` generation."""

    PERIODICITY_NONE = "none"
    PERIODICITY_DAILY = "daily"
    PERIODICITY_WEEKLY = "weekly"
    PERIODICITY_MONTHLY = "monthly"
    PERIODICITY_BIMONTHLY = "bimonthly"
    PERIODICITY_QUARTERLY = "quarterly"
    PERIODICITY_YEARLY = "yearly"
    PERIODICITY_CHOICES = [
        (PERIODICITY_NONE, "One-time"),
        (PERIODICITY_DAILY, "Daily"),
        (PERIODICITY_WEEKLY, "Weekly"),
        (PERIODICITY_MONTHLY, "Monthly"),
        (PERIODICITY_BIMONTHLY, "Bi-monthly (2 months)"),
        (PERIODICITY_QUARTERLY, "Quarterly"),
        (PERIODICITY_YEARLY, "Yearly"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_report_schedules",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_client_report_schedules",
    )
    periodicity = models.CharField(
        max_length=12, choices=PERIODICITY_CHOICES, default=PERIODICITY_NONE
    )
    language = models.CharField(
        max_length=12,
        choices=settings.LANGUAGES,
        default=default_report_language,
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=_("Title"),
    )
    email_recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
    chargers = models.ManyToManyField(
        "ocpp.Charger",
        blank=True,
        related_name="client_report_schedules",
    )
    periodic_task = models.OneToOneField(
        "django_celery_beat.PeriodicTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_report_schedule",
    )
    last_generated_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Client Report Schedule"
        verbose_name_plural = "Client Report Schedules"
        db_table = "core_clientreportschedule"

    @classmethod
    def label_for_periodicity(cls, value: str) -> str:
        lookup = dict(cls.PERIODICITY_CHOICES)
        return lookup.get(value, value)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        owner = self.owner.get_username() if self.owner else "Unassigned"
        return f"Client Report Schedule ({owner})"

    def save(self, *args, **kwargs):
        if self.language:
            self.language = normalize_report_language(self.language)
        self.title = normalize_report_title(self.title)
        sync = kwargs.pop("sync_task", True)
        super().save(*args, **kwargs)
        if sync and self.pk:
            self.sync_periodic_task()

    def delete(self, using=None, keep_parents=False):
        task_id = self.periodic_task_id
        super().delete(using=using, keep_parents=keep_parents)
        if task_id:
            from django_celery_beat.models import PeriodicTask

            PeriodicTask.objects.filter(pk=task_id).delete()

    def sync_periodic_task(self):
        """Ensure the Celery beat schedule matches the configured periodicity."""

        from django_celery_beat.models import CrontabSchedule, PeriodicTask
        from django.db import transaction
        import json as _json

        if self.periodicity == self.PERIODICITY_NONE:
            if self.periodic_task_id:
                PeriodicTask.objects.filter(pk=self.periodic_task_id).delete()
                type(self).objects.filter(pk=self.pk).update(periodic_task=None)
            return

        if self.periodicity == self.PERIODICITY_DAILY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="2",
                day_of_week="*",
                day_of_month="*",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_WEEKLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="3",
                day_of_week="1",
                day_of_month="*",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_MONTHLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_BIMONTHLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="1,3,5,7,9,11",
            )
        elif self.periodicity == self.PERIODICITY_QUARTERLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="1,4,7,10",
            )
        else:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="1",
            )

        raw_name = f"client_report_schedule_{self.pk}"
        name = normalize_periodic_task_name(PeriodicTask.objects, raw_name)
        defaults = {
            "crontab": schedule,
            "task": "apps.core.tasks.run_client_report_schedule",
            "kwargs": _json.dumps({"schedule_id": self.pk}),
            "enabled": True,
        }
        with transaction.atomic():
            periodic_task, _ = PeriodicTask.objects.update_or_create(
                name=name, defaults=defaults
            )
            if self.periodic_task_id != periodic_task.pk:
                type(self).objects.filter(pk=self.pk).update(
                    periodic_task=periodic_task
                )

    def calculate_period(self, reference=None):
        """Return the date range covered for the next execution."""

        from django.utils import timezone
        import datetime as _datetime

        ref_date = reference or timezone.localdate()

        if self.periodicity == self.PERIODICITY_DAILY:
            end = ref_date - _datetime.timedelta(days=1)
            start = end
        elif self.periodicity == self.PERIODICITY_WEEKLY:
            start_of_week = ref_date - _datetime.timedelta(days=ref_date.weekday())
            end = start_of_week - _datetime.timedelta(days=1)
            start = end - _datetime.timedelta(days=6)
        else:
            period_months = self._period_months()
            if period_months:
                start, end = self._calculate_month_period(ref_date, period_months)
            else:
                raise ValueError("calculate_period called for non-recurring schedule")

        return start, end

    def _advance_period(
        self, start: datetime_date, end: datetime_date
    ) -> tuple[datetime_date, datetime_date]:
        import calendar as _calendar
        import datetime as _datetime

        if self.periodicity == self.PERIODICITY_DAILY:
            delta = _datetime.timedelta(days=1)
            return start + delta, end + delta
        if self.periodicity == self.PERIODICITY_WEEKLY:
            delta = _datetime.timedelta(days=7)
            return start + delta, end + delta
        period_months = self._period_months()
        if period_months:
            base_start = start.replace(day=1)
            next_start = self._add_months(base_start, period_months)
            next_end_start = self._add_months(next_start, period_months)
            next_end = next_end_start - _datetime.timedelta(days=1)
            return next_start, next_end
        raise ValueError("advance_period called for non-recurring schedule")

    def _period_months(self) -> int | None:
        return {
            self.PERIODICITY_MONTHLY: 1,
            self.PERIODICITY_BIMONTHLY: 2,
            self.PERIODICITY_QUARTERLY: 3,
            self.PERIODICITY_YEARLY: 12,
        }.get(self.periodicity)

    def _calculate_month_period(
        self, ref_date: datetime_date, months: int
    ) -> tuple[datetime_date, datetime_date]:
        import calendar as _calendar
        import datetime as _datetime

        first_of_month = ref_date.replace(day=1)
        end = first_of_month - _datetime.timedelta(days=1)

        months_into_block = (end.month - 1) % months + 1
        if months_into_block < months:
            end_anchor = self._add_months(end.replace(day=1), -months_into_block)
            end_day = _calendar.monthrange(end_anchor.year, end_anchor.month)[1]
            end = end_anchor.replace(day=end_day)

        start_anchor = self._add_months(end.replace(day=1), -(months - 1))
        start = start_anchor.replace(day=1)
        return start, end

    @staticmethod
    def _add_months(base: datetime_date, months: int) -> datetime_date:
        import calendar as _calendar

        month_index = base.month - 1 + months
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        last_day = _calendar.monthrange(year, month)[1]
        day = min(base.day, last_day)
        return base.replace(year=year, month=month, day=day)

    def iter_pending_periods(self, reference=None):
        from django.utils import timezone

        if self.periodicity == self.PERIODICITY_NONE:
            return []

        ref_date = reference or timezone.localdate()
        try:
            target_start, target_end = self.calculate_period(reference=ref_date)
        except ValueError:
            return []

        reports = self.reports.order_by("start_date", "end_date")
        last_report = reports.last()
        if last_report:
            current_start, current_end = self._advance_period(
                last_report.start_date, last_report.end_date
            )
        else:
            current_start, current_end = target_start, target_end

        if current_end < current_start:
            return []

        pending: list[tuple[datetime.date, datetime.date]] = []
        safety = 0
        while current_end <= target_end:
            exists = reports.filter(
                start_date=current_start, end_date=current_end
            ).exists()
            if not exists:
                pending.append((current_start, current_end))
            try:
                current_start, current_end = self._advance_period(
                    current_start, current_end
                )
            except ValueError:
                break
            safety += 1
            if safety > 400:
                break

        return pending

    def resolve_recipients(self):
        """Return (to, cc) email lists respecting owner fallbacks."""

        from django.contrib.auth import get_user_model

        to: list[str] = []
        cc: list[str] = []
        seen: set[str] = set()

        for email in self.email_recipients:
            normalized = (email or "").strip()
            if not normalized:
                continue
            if normalized.lower() in seen:
                continue
            to.append(normalized)
            seen.add(normalized.lower())

        owner_email = None
        if self.owner and self.owner.email:
            candidate = self.owner.email.strip()
            if candidate:
                owner_email = candidate

        if to:
            if owner_email and owner_email.lower() not in seen:
                cc.append(owner_email)
        else:
            if owner_email:
                to.append(owner_email)
                seen.add(owner_email.lower())
            else:
                admin_email = (
                    get_user_model()
                    .objects.filter(is_superuser=True, is_active=True)
                    .exclude(email="")
                    .values_list("email", flat=True)
                    .first()
                )
                if admin_email:
                    to.append(admin_email)
                    seen.add(admin_email.lower())
                elif settings.DEFAULT_FROM_EMAIL:
                    to.append(settings.DEFAULT_FROM_EMAIL)

        return to, cc

    def resolve_reply_to(self) -> list[str]:
        return ClientReport.resolve_reply_to_for_owner(self.owner)

    def get_outbox(self):
        """Return the preferred :class:`teams.models.EmailOutbox` instance."""

        return ClientReport.resolve_outbox_for_owner(self.owner)

    def notify_failure(self, message: str):
        from nodes.models import NetMessage

        NetMessage.broadcast("Client report delivery issue", message)

    def run(self, *, start: datetime_date | None = None, end: datetime_date | None = None):
        """Generate the report, persist it and deliver notifications."""

        if start is None or end is None:
            try:
                start, end = self.calculate_period()
            except ValueError:
                return None

        try:
            report = ClientReport.generate(
                start,
                end,
                owner=self.owner,
                schedule=self,
                recipients=self.email_recipients,
                disable_emails=self.disable_emails,
                chargers=list(self.chargers.all()),
                language=self.language,
                title=self.title,
            )
            report.chargers.set(self.chargers.all())
            report.store_local_copy()
        except Exception as exc:
            self.notify_failure(str(exc))
            raise

        if not self.disable_emails:
            to, cc = self.resolve_recipients()
            if not to:
                self.notify_failure("No recipients available for client report")
                raise RuntimeError("No recipients available for client report")
            else:
                try:
                    from apps.core.models import ClientReport as ProxyClientReport

                    delivered = ProxyClientReport.send_delivery(
                        report,
                        to=to,
                        cc=cc,
                        outbox=self.get_outbox(),
                        reply_to=self.resolve_reply_to(),
                    )
                    if delivered:
                        type(report).objects.filter(pk=report.pk).update(
                            recipients=delivered
                        )
                        report.recipients = delivered
                except Exception as exc:
                    self.notify_failure(str(exc))
                    raise

        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(last_generated_on=now)
        self.last_generated_on = now
        return report

    def generate_missing_reports(self, reference=None):
        generated: list["ClientReport"] = []
        for start, end in self.iter_pending_periods(reference=reference):
            report = self.run(start=start, end=end)
            if report:
                generated.append(report)
        return generated


class ClientReport(Entity):
    """Snapshot of energy usage over a period."""

    start_date = models.DateField()
    end_date = models.DateField()
    created_on = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=dict)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_reports",
    )
    schedule = models.ForeignKey(
        "ClientReportSchedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    language = models.CharField(
        max_length=12,
        choices=settings.LANGUAGES,
        default=default_report_language,
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=_("Title"),
    )
    recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
    chargers = models.ManyToManyField(
        "ocpp.Charger",
        blank=True,
        related_name="client_reports",
    )

    class Meta:
        verbose_name = _("Consumer Report")
        verbose_name_plural = _("Consumer Reports")
        db_table = "core_client_report"
        ordering = ["-created_on"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        period_type = (
            self.schedule.periodicity
            if self.schedule
            else ClientReportSchedule.PERIODICITY_NONE
        )
        return f"{self.start_date} - {self.end_date} ({period_type})"

    @staticmethod
    def default_language() -> str:
        return default_report_language()

    @staticmethod
    def normalize_language(language: str | None) -> str:
        return normalize_report_language(language)

    @staticmethod
    def normalize_title(title: str | None) -> str:
        return normalize_report_title(title)

    def save(self, *args, **kwargs):
        if self.language:
            self.language = normalize_report_language(self.language)
        self.title = self.normalize_title(self.title)
        super().save(*args, **kwargs)

    @property
    def periodicity_label(self) -> str:
        if self.schedule:
            return self.schedule.get_periodicity_display()
        return ClientReportSchedule.label_for_periodicity(
            ClientReportSchedule.PERIODICITY_NONE
        )

    @property
    def total_kw_period(self) -> float:
        totals = (self.rows_for_display or {}).get("totals", {})
        return float(totals.get("total_kw_period", 0.0) or 0.0)

    @classmethod
    def generate(
        cls,
        start_date,
        end_date,
        *,
        owner=None,
        schedule=None,
        recipients: list[str] | None = None,
        disable_emails: bool = False,
        chargers=None,
        language: str | None = None,
        title: str | None = None,
    ):
        from collections.abc import Iterable as _Iterable

        charger_list = []
        if chargers:
            if isinstance(chargers, _Iterable):
                charger_list = list(chargers)
            else:
                charger_list = [chargers]

        payload = cls.build_rows(start_date, end_date, chargers=charger_list)
        normalized_language = cls.normalize_language(language)
        title_value = cls.normalize_title(title)
        report = cls.objects.create(
            start_date=start_date,
            end_date=end_date,
            data=payload,
            owner=owner,
            schedule=schedule,
            recipients=list(recipients or []),
            disable_emails=disable_emails,
            language=normalized_language,
            title=title_value,
        )
        if charger_list:
            report.chargers.set(charger_list)
        return report

    def store_local_copy(self, html: str | None = None):
        """Persist the report data and optional HTML rendering to disk."""

        import json as _json
        from django.template.loader import render_to_string

        base_dir = Path(settings.BASE_DIR)
        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"

        language_code = self.normalize_language(self.language)
        context = {
            "report": self,
            "language_code": language_code,
            "default_language": type(self).default_language(),
        }
        with override(language_code):
            html_content = html or render_to_string(
                "core/reports/client_report_email.html", context
            )
        html_path = report_dir / f"{identifier}.html"
        html_path.write_text(html_content, encoding="utf-8")

        json_path = report_dir / f"{identifier}.json"
        json_path.write_text(
            _json.dumps(self.data, indent=2, default=str), encoding="utf-8"
        )

        pdf_path = report_dir / f"{identifier}.pdf"
        self.render_pdf(pdf_path)

        export = {
            "html_path": ClientReport._relative_to_base(html_path, base_dir),
            "json_path": ClientReport._relative_to_base(json_path, base_dir),
            "pdf_path": ClientReport._relative_to_base(pdf_path, base_dir),
        }

        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return export, html_content

    def send_delivery(
        self,
        *,
        to: list[str] | tuple[str, ...],
        cc: list[str] | tuple[str, ...] | None = None,
        outbox=None,
        reply_to: list[str] | None = None,
    ) -> list[str]:
        from apps.core import mailer

        recipients = list(to or [])
        if not recipients:
            return []

        pdf_path = self.ensure_pdf()
        attachments = [
            (pdf_path.name, pdf_path.read_bytes(), "application/pdf"),
        ]

        language_code = self.normalize_language(self.language)
        with override(language_code):
            totals = self.rows_for_display.get("totals", {})
            start_display = formats.date_format(
                self.start_date, format="DATE_FORMAT", use_l10n=True
            )
            end_display = formats.date_format(
                self.end_date, format="DATE_FORMAT", use_l10n=True
            )
            total_kw_period_label = gettext("Total kW during period")
            total_kw_all_label = gettext("Total kW (all time)")
            report_title = self.normalize_title(self.title) or gettext(
                "Consumer Report"
            )
            body_lines = [
                gettext("%(title)s for %(start)s through %(end)s.")
                % {"title": report_title, "start": start_display, "end": end_display},
                f"{total_kw_period_label}: "
                f"{formats.number_format(totals.get('total_kw_period', 0.0), decimal_pos=2, use_l10n=True)}.",
                f"{total_kw_all_label}: "
                f"{formats.number_format(totals.get('total_kw', 0.0), decimal_pos=2, use_l10n=True)}.",
            ]
            message = "\n".join(body_lines)
            subject = gettext("%(title)s %(start)s - %(end)s") % {
                "title": report_title,
                "start": start_display,
                "end": end_display,
            }

        kwargs = {}
        if reply_to:
            kwargs["reply_to"] = reply_to

        mailer.send(
            subject,
            message,
            recipients,
            outbox=outbox,
            cc=list(cc or []),
            attachments=attachments,
            **kwargs,
        )

        delivered = list(dict.fromkeys(recipients + list(cc or [])))
        return delivered

    @staticmethod
    def build_rows(
        start_date=None,
        end_date=None,
        *,
        for_display: bool = False,
        chargers=None,
    ):
        dataset = ClientReport._build_dataset(start_date, end_date, chargers=chargers)
        if for_display:
            return ClientReport._normalize_dataset_for_display(dataset)
        return dataset

    @staticmethod
    def _build_dataset(start_date=None, end_date=None, *, chargers=None):
        from datetime import datetime, time, timedelta, timezone as pytimezone
        from ocpp.models import Transaction, annotate_transaction_energy_bounds

        Charger = apps.get_model("ocpp", "Charger")
        RFID = apps.get_model("core", "RFID")

        qs = Transaction.objects.all()

        start_dt = None
        end_dt = None
        if start_date:
            start_dt = datetime.combine(start_date, time.min, tzinfo=pytimezone.utc)
            qs = qs.filter(start_time__gte=start_dt)
        if end_date:
            end_dt = datetime.combine(
                end_date + timedelta(days=1), time.min, tzinfo=pytimezone.utc
            )
            qs = qs.filter(start_time__lt=end_dt)

        selected_base_ids = None
        if chargers:
            selected_base_ids = {
                charger.charger_id for charger in chargers if charger.charger_id
            }
            if selected_base_ids:
                qs = qs.filter(charger__charger_id__in=selected_base_ids)

        qs = qs.select_related("account", "charger")
        qs = annotate_transaction_energy_bounds(
            qs,
            start_field="report_meter_energy_start",
            end_field="report_meter_energy_end",
        )
        transactions = list(qs.order_by("start_time", "pk"))

        rfid_values = {tx.rfid for tx in transactions if tx.rfid}
        tag_map: dict[str, RFID] = {}
        if rfid_values:
            tag_map = {
                tag.rfid: tag
                for tag in RFID.objects.filter(rfid__in=rfid_values).prefetch_related(
                    "energy_accounts"
                )
            }

        charger_ids = {
            tx.charger.charger_id
            for tx in transactions
            if getattr(tx, "charger", None) and tx.charger.charger_id
        }
        aggregator_map: dict[str, Charger] = {}
        if charger_ids:
            aggregator_map = {
                charger.charger_id: charger
                for charger in Charger.objects.filter(
                    charger_id__in=charger_ids, connector_id__isnull=True
                )
            }

        groups: dict[str, dict[str, Any]] = {}
        for tx in transactions:
            charger = getattr(tx, "charger", None)
            if charger is None:
                continue
            base_id = charger.charger_id
            if selected_base_ids is not None and base_id not in selected_base_ids:
                continue
            aggregator = aggregator_map.get(base_id) or charger
            entry = groups.setdefault(
                base_id,
                {"charger": aggregator, "transactions": []},
            )
            entry["transactions"].append(tx)

        evcs_entries: list[dict[str, Any]] = []
        total_all_time = 0.0
        total_period = 0.0

        def _sort_key(tx):
            anchor = getattr(tx, "start_time", None)
            if anchor is None:
                anchor = datetime.min.replace(tzinfo=pytimezone.utc)
            return (anchor, tx.pk or 0)

        for base_id, info in sorted(groups.items(), key=lambda item: item[0]):
            aggregator = info["charger"]
            txs = sorted(info["transactions"], key=_sort_key)
            total_kw_all = float(getattr(aggregator, "total_kw", 0.0) or 0.0)
            total_kw_period = 0.0
            if hasattr(aggregator, "total_kw_for_range"):
                total_kw_period = float(
                    aggregator.total_kw_for_range(start=start_dt, end=end_dt) or 0.0
                )
            total_all_time += total_kw_all
            total_period += total_kw_period

            session_rows: list[dict[str, Any]] = []
            for tx in txs:
                session_kw = float(getattr(tx, "kw", 0.0) or 0.0)
                if session_kw <= 0:
                    continue

                start_kwh, end_kwh = ClientReport._resolve_meter_bounds(tx)

                connector_number = (
                    tx.connector_id
                    if getattr(tx, "connector_id", None) is not None
                    else getattr(getattr(tx, "charger", None), "connector_id", None)
                )
                connector_letter = (
                    Charger.connector_letter_from_value(connector_number)
                    if connector_number not in {None, ""}
                    else None
                )
                connector_order = (
                    connector_number
                    if isinstance(connector_number, int)
                    else None
                )

                rfid_value = (tx.rfid or "").strip()
                tag = tag_map.get(rfid_value)
                label = None
                account_name = (
                    tx.account.name
                    if tx.account and getattr(tx.account, "name", None)
                    else None
                )
                if tag:
                    label = tag.custom_label or str(tag.label_id)
                    if not account_name:
                        account = next(iter(tag.energy_accounts.all()), None)
                        if account and getattr(account, "name", None):
                            account_name = account.name
                elif rfid_value:
                    label = rfid_value

                session_rows.append(
                    {
                        "connector": connector_number,
                        "connector_label": connector_letter,
                        "connector_order": connector_order,
                        "rfid_label": label,
                        "account_name": account_name,
                        "start_kwh": start_kwh,
                        "end_kwh": end_kwh,
                        "session_kwh": session_kw,
                        "start": tx.start_time.isoformat()
                        if getattr(tx, "start_time", None)
                        else None,
                        "end": tx.stop_time.isoformat()
                        if getattr(tx, "stop_time", None)
                        else None,
                    }
                )

            evcs_entries.append(
                {
                    "charger_id": aggregator.pk,
                    "serial_number": aggregator.charger_id,
                    "display_name": aggregator.display_name
                    or aggregator.name
                    or aggregator.charger_id,
                    "total_kw": total_kw_all,
                    "total_kw_period": total_kw_period,
                    "transactions": session_rows,
                }
            )

        filters: dict[str, Any] = {}
        if selected_base_ids:
            filters["chargers"] = sorted(selected_base_ids)

        return {
            "schema": "evcs-session/v1",
            "evcs": evcs_entries,
            "totals": {
                "total_kw": total_all_time,
                "total_kw_period": total_period,
            },
            "filters": filters,
        }

    @staticmethod
    def _resolve_meter_bounds(tx) -> tuple[float | None, float | None]:
        def _convert(value):
            if value in {None, ""}:
                return None
            try:
                return float(value) / 1000.0
            except (TypeError, ValueError):
                return None

        start_value = _convert(getattr(tx, "meter_start", None))
        end_value = _convert(getattr(tx, "meter_stop", None))

        def _coerce_energy(value):
            if value in {None, ""}:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        if start_value is None:
            annotated_start = getattr(tx, "report_meter_energy_start", None)
            start_value = _coerce_energy(annotated_start)

        if end_value is None:
            annotated_end = getattr(tx, "report_meter_energy_end", None)
            end_value = _coerce_energy(annotated_end)

        if start_value is None or end_value is None:
            readings_manager = getattr(tx, "meter_values", None)
            if readings_manager is not None:
                qs = readings_manager.filter(energy__isnull=False).order_by("timestamp")
                if start_value is None:
                    first_energy = qs.values_list("energy", flat=True).first()
                    start_value = _coerce_energy(first_energy)
                if end_value is None:
                    last_energy = qs.order_by("-timestamp").values_list(
                        "energy", flat=True
                    ).first()
                    end_value = _coerce_energy(last_energy)

        return start_value, end_value

    @staticmethod
    def _format_session_datetime(value):
        if not value:
            return None
        localized = timezone.localtime(value)
        date_part = formats.date_format(
            localized, format="MONTH_DAY_FORMAT", use_l10n=True
        )
        time_part = formats.time_format(
            localized, format="TIME_FORMAT", use_l10n=True
        )
        return gettext("%(date)s, %(time)s") % {
            "date": date_part,
            "time": time_part,
        }

    @staticmethod
    def _calculate_duration_minutes(start, end):
        if not start or not end:
            return None
        total_seconds = (end - start).total_seconds()
        if total_seconds < 0:
            return None
        return int(round(total_seconds / 60.0))

    @staticmethod
    def _normalize_dataset_for_display(dataset: dict[str, Any]):
        schema = dataset.get("schema")
        if schema == "evcs-session/v1":
            from datetime import datetime

            evcs_entries: list[dict[str, Any]] = []
            for entry in dataset.get("evcs", []):
                normalized_rows: list[dict[str, Any]] = []
                for row in entry.get("transactions", []):
                    start_val = row.get("start")
                    end_val = row.get("end")

                    start_dt = None
                    if start_val:
                        start_dt = parse_datetime(start_val)
                        if start_dt and timezone.is_naive(start_dt):
                            start_dt = timezone.make_aware(start_dt, timezone.utc)

                    end_dt = None
                    if end_val:
                        end_dt = parse_datetime(end_val)
                        if end_dt and timezone.is_naive(end_dt):
                            end_dt = timezone.make_aware(end_dt, timezone.utc)

                    normalized_rows.append(
                        {
                            "connector": row.get("connector"),
                            "connector_label": row.get("connector_label"),
                            "connector_order": row.get("connector_order"),
                            "rfid_label": row.get("rfid_label"),
                            "account_name": row.get("account_name"),
                            "start_kwh": row.get("start_kwh"),
                            "end_kwh": row.get("end_kwh"),
                            "session_kwh": row.get("session_kwh"),
                            "start": start_dt,
                            "end": end_dt,
                            "start_display": ClientReport._format_session_datetime(
                                start_dt
                            ),
                            "end_display": ClientReport._format_session_datetime(
                                end_dt
                            ),
                            "duration_minutes": ClientReport._calculate_duration_minutes(
                                start_dt, end_dt
                            ),
                        }
                    )

                def _connector_sort_value(item):
                    order_value = item.get("connector_order")
                    if isinstance(order_value, int):
                        return order_value
                    connector_value = item.get("connector")
                    if isinstance(connector_value, int):
                        return connector_value
                    try:
                        return int(connector_value)
                    except (TypeError, ValueError):
                        return 0

                normalized_rows.sort(
                    key=lambda item: (
                        item["start"]
                        if item["start"] is not None
                        else datetime.min.replace(tzinfo=timezone.utc),
                        _connector_sort_value(item),
                    )
                )

                evcs_entries.append(
                    {
                        "display_name": entry.get("display_name")
                        or entry.get("serial_number")
                        or "Charge Point",
                        "serial_number": entry.get("serial_number"),
                        "total_kw": entry.get("total_kw", 0.0),
                        "total_kw_period": entry.get("total_kw_period", 0.0),
                        "transactions": normalized_rows,
                    }
                )

            totals = dataset.get("totals", {})
            return {
                "schema": schema,
                "evcs": evcs_entries,
                "totals": {
                    "total_kw": totals.get("total_kw", 0.0),
                    "total_kw_period": totals.get("total_kw_period", 0.0),
                },
                "filters": dataset.get("filters", {}),
            }

        if schema == "session-list/v1":
            parsed: list[dict[str, Any]] = []
            for row in dataset.get("rows", []):
                item = dict(row)
                start_val = row.get("start")
                end_val = row.get("end")

                if start_val:
                    start_dt = parse_datetime(start_val)
                    if start_dt and timezone.is_naive(start_dt):
                        start_dt = timezone.make_aware(start_dt, timezone.utc)
                    item["start"] = start_dt
                else:
                    start_dt = None
                    item["start"] = None

                if end_val:
                    end_dt = parse_datetime(end_val)
                    if end_dt and timezone.is_naive(end_dt):
                        end_dt = timezone.make_aware(end_dt, timezone.utc)
                    item["end"] = end_dt
                else:
                    end_dt = None
                    item["end"] = None

                item["start_display"] = ClientReport._format_session_datetime(start_dt)
                item["end_display"] = ClientReport._format_session_datetime(end_dt)
                item["duration_minutes"] = ClientReport._calculate_duration_minutes(
                    start_dt, end_dt
                )

                parsed.append(item)

            return {"schema": schema, "rows": parsed}

        return {
            "schema": schema,
            "rows": dataset.get("rows", []),
            "filters": dataset.get("filters", {}),
        }

    @staticmethod
    def build_evcs_summary_rows(dataset: dict[str, Any] | None):
        """Flatten EVCS session data for summarized presentations."""

        if not dataset or dataset.get("schema") != "evcs-session/v1":
            return []

        summary_rows: list[dict[str, Any]] = []
        for entry in dataset.get("evcs", []):
            if not isinstance(entry, dict):
                continue

            display_name = (
                entry.get("display_name")
                or entry.get("serial_number")
                or gettext("Charge Point")
            )
            serial_number = entry.get("serial_number")
            transactions = entry.get("transactions") or []
            if not isinstance(transactions, list):
                continue

            for row in transactions:
                if not isinstance(row, dict):
                    continue
                summary_rows.append(
                    {
                        "display_name": display_name,
                        "serial_number": serial_number,
                        "transaction": row,
                    }
                )

        return summary_rows


    @property
    def rows_for_display(self):
        data = self.data or {}
        return ClientReport._normalize_dataset_for_display(data)

    @staticmethod
    def _relative_to_base(path: Path, base_dir: Path) -> str:
        try:
            return str(path.relative_to(base_dir))
        except ValueError:
            return str(path)

    @classmethod
    def _load_pdf_template(cls, language_code: str | None) -> dict[str, str]:
        from django.template import TemplateDoesNotExist
        from django.template.loader import render_to_string

        candidates: list[str] = []

        requested = (language_code or "").strip().replace("_", "-")
        base_language = requested.split("-", 1)[0] if requested else ""
        normalized = cls.normalize_language(language_code)

        for code in (requested, base_language, normalized):
            if code:
                candidates.append(code)

        default_code = default_report_language()
        if default_code:
            candidates.append(default_code)

        candidates.append("en")

        for code in dict.fromkeys(candidates):
            template_name = f"core/reports/client_report_pdf/{code}.json"
            try:
                rendered = render_to_string(template_name)
            except TemplateDoesNotExist:
                continue
            if not rendered:
                continue
            try:
                data = json.loads(rendered)
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid client report PDF template %s", template_name, exc_info=True
                )
                continue
            if isinstance(data, dict):
                return data

        return {}

    @staticmethod
    def resolve_reply_to_for_owner(owner) -> list[str]:
        if not owner:
            return []
        try:
            inbox_model = apps.get_model("teams", "EmailInbox")
        except LookupError:
            inbox_model = None
        try:
            inbox = owner.get_profile(inbox_model) if inbox_model else None
        except Exception:  # pragma: no cover - defensive catch
            inbox = None
        if inbox and getattr(inbox, "username", ""):
            address = inbox.username.strip()
            if address:
                return [address]
        return []

    @staticmethod
    def resolve_outbox_for_owner(owner):
        from nodes.models import Node

        try:
            outbox_model = apps.get_model("teams", "EmailOutbox")
        except LookupError:
            outbox_model = None

        if owner:
            try:
                outbox = owner.get_profile(outbox_model) if outbox_model else None
            except Exception:  # pragma: no cover - defensive catch
                outbox = None
            if outbox:
                return outbox

        node = Node.get_local()
        if node:
            return getattr(node, "email_outbox", None)
        return None

    def render_pdf(self, target: Path):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        dataset = self.rows_for_display
        schema = dataset.get("schema")

        language_code = self.normalize_language(self.language)
        with override(language_code):
            styles = getSampleStyleSheet()
            title_style = styles["Title"]
            subtitle_style = styles["Heading2"]
            normal_style = styles["BodyText"]
            emphasis_style = styles["Heading3"]

            document = SimpleDocTemplate(
                str(target_path),
                pagesize=landscape(letter),
                leftMargin=0.5 * inch,
                rightMargin=0.5 * inch,
                topMargin=0.6 * inch,
                bottomMargin=0.5 * inch,
            )

            story: list = []
            labels = self._load_pdf_template(language_code)

            def label(key: str, default: str) -> str:
                value = labels.get(key) if isinstance(labels, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
                return gettext(default)

            report_title = self.normalize_title(self.title) or label(
                "title", "Consumer Report"
            )
            story.append(Paragraph(report_title, title_style))

            start_display = formats.date_format(
                self.start_date, format="DATE_FORMAT", use_l10n=True
            )
            end_display = formats.date_format(
                self.end_date, format="DATE_FORMAT", use_l10n=True
            )
            default_period_text = gettext("Period: %(start)s to %(end)s") % {
                "start": start_display,
                "end": end_display,
            }
            period_template = labels.get("period") if isinstance(labels, dict) else None
            if isinstance(period_template, str):
                try:
                    period_text = period_template.format(
                        start=start_display, end=end_display
                    )
                except (KeyError, IndexError, ValueError):
                    logger.warning(
                        "Invalid period template for client report PDF: %s",
                        period_template,
                    )
                    period_text = default_period_text
            else:
                period_text = default_period_text
            story.append(Paragraph(period_text, emphasis_style))
            story.append(Spacer(1, 0.25 * inch))

            total_kw_all_time_label = label("total_kw_all_time", "Total kW (all time)")
            total_kw_period_label = label("total_kw_period", "Total kW (period)")
            connector_label = label("connector", "Connector")
            account_label = label("account", "Account")
            session_kwh_label = label("session_kwh", "Session kW")
            session_start_label = label("session_start", "Session start")
            session_end_label = label("session_end", "Session end")
            time_label = label("time", "Time")
            rfid_label = label("rfid_label", "RFID label")
            no_sessions_period = label(
                "no_sessions_period",
                "No charging sessions recorded for the selected period.",
            )
            no_sessions_point = label(
                "no_sessions_point",
                "No charging sessions recorded for this charge point.",
            )
            no_structured_data = label(
                "no_structured_data",
                "No structured data is available for this report.",
            )
            report_totals_label = label("report_totals", "Report totals")
            total_kw_period_line = label(
                "total_kw_period_line", "Total kW during period"
            )
            charge_point_label = label("charge_point", "Charge Point")
            serial_template = (
                labels.get("charge_point_serial")
                if isinstance(labels, dict)
                else None
            )

            def format_datetime(value):
                if not value:
                    return "—"
                return ClientReport._format_session_datetime(value) or "—"

            def format_decimal(value):
                if value is None:
                    return "—"
                return formats.number_format(value, decimal_pos=2, use_l10n=True)

            def format_duration(value):
                if value is None:
                    return "—"
                return formats.number_format(value, decimal_pos=0, use_l10n=True)

            if schema == "evcs-session/v1":
                evcs_entries = dataset.get("evcs", [])
                if not evcs_entries:
                    story.append(Paragraph(no_sessions_period, normal_style))
                for index, evcs in enumerate(evcs_entries):
                    if index:
                        story.append(Spacer(1, 0.2 * inch))

                    display_name = evcs.get("display_name") or charge_point_label
                    serial_number = evcs.get("serial_number")
                    if serial_number:
                        if isinstance(serial_template, str):
                            try:
                                header_text = serial_template.format(
                                    name=display_name, serial=serial_number
                                )
                            except (KeyError, IndexError, ValueError):
                                header_text = serial_template
                        else:
                            header_text = gettext("%(name)s (Serial: %(serial)s)") % {
                                "name": display_name,
                                "serial": serial_number,
                            }
                    else:
                        header_text = display_name
                    story.append(Paragraph(header_text, subtitle_style))

                    metrics_text = (
                        f"{total_kw_all_time_label}: "
                        f"{format_decimal(evcs.get('total_kw', 0.0))} | "
                        f"{total_kw_period_label}: "
                        f"{format_decimal(evcs.get('total_kw_period', 0.0))}"
                    )
                    story.append(Paragraph(metrics_text, normal_style))
                    story.append(Spacer(1, 0.1 * inch))

                    transactions = evcs.get("transactions", [])
                    if transactions:
                        table_data = [
                            [
                                session_kwh_label,
                                session_start_label,
                                session_end_label,
                                time_label,
                                connector_label,
                                rfid_label,
                                account_label,
                            ]
                        ]

                        for row in transactions:
                            start_dt = row.get("start")
                            end_dt = row.get("end")
                            duration_value = row.get("duration_minutes")
                            table_data.append(
                                [
                                    format_decimal(row.get("session_kwh")),
                                    format_datetime(start_dt),
                                    format_datetime(end_dt),
                                    format_duration(duration_value),
                                    (
                                        row.get("connector_label")
                                        or row.get("connector")
                                    )
                                    if row.get("connector") is not None
                                    or row.get("connector_label")
                                    else "—",
                                    row.get("rfid_label") or "—",
                                    row.get("account_name") or "—",
                                ]
                            )

                        column_count = len(table_data[0])
                        col_width = document.width / column_count if column_count else None
                        table = Table(
                            table_data,
                            repeatRows=1,
                            colWidths=[col_width] * column_count if col_width else None,
                            hAlign="LEFT",
                        )
                        table.setStyle(
                            TableStyle(
                                [
                                    (
                                        "BACKGROUND",
                                        (0, 0),
                                        (-1, 0),
                                        colors.HexColor("#0f172a"),
                                    ),
                                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                                    (
                                        "ROWBACKGROUNDS",
                                        (0, 1),
                                        (-1, -1),
                                        [colors.whitesmoke, colors.HexColor("#eef2ff")],
                                    ),
                                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                    ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                                ]
                            )
                        )
                        story.append(table)
                    else:
                        story.append(Paragraph(no_sessions_point, normal_style))
            else:
                story.append(Paragraph(no_structured_data, normal_style))

            totals = dataset.get("totals") or {}
            story.append(Spacer(1, 0.3 * inch))
            story.append(Paragraph(report_totals_label, emphasis_style))
            story.append(
                Paragraph(
                    f"{total_kw_all_time_label}: "
                    f"{format_decimal(totals.get('total_kw', 0.0))}",
                    emphasis_style,
                )
            )
            story.append(
                Paragraph(
                    f"{total_kw_period_line}: "
                    f"{format_decimal(totals.get('total_kw_period', 0.0))}",
                    emphasis_style,
                )
            )

            document.build(story)

    def ensure_pdf(self) -> Path:
        base_dir = Path(settings.BASE_DIR)
        export = dict((self.data or {}).get("export") or {})
        pdf_relative = export.get("pdf_path")
        if pdf_relative:
            candidate = base_dir / pdf_relative
            if candidate.exists():
                return candidate

        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"
        pdf_path = report_dir / f"{identifier}.pdf"
        self.render_pdf(pdf_path)

        export["pdf_path"] = ClientReport._relative_to_base(pdf_path, base_dir)
        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return pdf_path
