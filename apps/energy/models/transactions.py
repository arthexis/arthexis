from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

from apps.core.entity import Entity

if TYPE_CHECKING:
    from .reporting import ClientReport


class EnergyTransaction(Entity):
    """Unified ledger entry for kW credits and debits."""

    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class Source(models.TextChoices):
        CARD_TOPUP = "card_topup", "Card top-up"
        SUBSCRIPTION = "subscription", "Subscription"
        SESSION_CLOSE = "session_close", "Session close"
        MANUAL_ADJUSTMENT = "manual_adjustment", "Manual adjustment"

    account = models.ForeignKey(
        "energy.CustomerAccount",
        on_delete=models.CASCADE,
        related_name="energy_transactions",
    )
    tariff = models.ForeignKey(
        "energy.EnergyTariff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="energy_transactions",
        help_text="Tariff in effect when the purchase occurred.",
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.CREDIT,
        help_text="Whether this ledger entry is a credit or debit.",
    )
    delta_kw = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text="Signed kW impact on the account balance (credits positive).",
    )
    charged_amount_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Currency amount for the transaction when applicable.",
    )
    conversion_factor = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Conversion factor (kW per MXN) applied at purchase time.",
    )
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.MANUAL_ADJUSTMENT,
        help_text="Originating flow for this ledger entry.",
    )
    reference = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional reference identifier for reconciliation.",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Energy Transaction"
        verbose_name_plural = "Energy Transactions"
        ordering = ("-created_on",)
        db_table = "core_energytransaction"
        indexes = [
            models.Index(
                fields=["account", "created_on", "direction"],
                name="energy_txn_acct_dir_idx",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.delta_kw} kW ({self.direction}) on {self.created_on:%Y-%m-%d}"


def generate_missing_reports(schedule, reference=None) -> list["ClientReport"]:
    generated: list["ClientReport"] = []
    for start, end in schedule.iter_pending_periods(reference=reference):
        report = schedule.run(start=start, end=end)
        if report:
            generated.append(report)
    return generated
