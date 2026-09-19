"""Retained OCPP 1.6 configuration and OCPP 2.0.1 variable records."""

from django.db import models

from apps.ocpp.models.assets import Charger


class ChargerVariable(models.Model):
    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="variables"
    )
    component = models.CharField(max_length=120)
    variable = models.CharField(max_length=120)
    attribute_type = models.CharField(max_length=30, default="Actual")
    value = models.TextField(blank=True)
    mutable = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("charger", "component", "variable", "attribute_type"),
                name="unique_charger_variable",
            )
        ]
