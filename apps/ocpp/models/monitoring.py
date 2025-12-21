from __future__ import annotations

from .base import *


class Variable(Entity):
    """Persisted component/variable values reported by charge points."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="ocpp_variables",
    )
    component_name = models.CharField(_("Component"), max_length=200)
    component_instance = models.CharField(
        _("Component Instance"),
        max_length=200,
        blank=True,
        default="",
    )
    variable_name = models.CharField(_("Variable"), max_length=200)
    variable_instance = models.CharField(
        _("Variable Instance"),
        max_length=200,
        blank=True,
        default="",
    )
    attribute_type = models.CharField(
        _("Attribute Type"),
        max_length=32,
        blank=True,
        default="",
    )
    attribute_status = models.CharField(
        _("Attribute Status"),
        max_length=32,
        blank=True,
        default="",
    )
    value = models.TextField(_("Value"), blank=True, default="")
    value_type = models.CharField(
        _("Value Type"),
        max_length=32,
        blank=True,
        default="",
    )
    created_on = models.DateTimeField(auto_now_add=True, verbose_name=_("Created on"))
    updated_on = models.DateTimeField(auto_now=True, verbose_name=_("Updated on"))

    class Meta:
        ordering = ["component_name", "variable_name", "pk"]
        verbose_name = _("Variable")
        verbose_name_plural = _("Variables")
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "charger",
                    "component_name",
                    "component_instance",
                    "variable_name",
                    "variable_instance",
                    "attribute_type",
                ],
                name="ocpp_variable_unique_component",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        component = self.component_name
        if self.component_instance:
            component = f"{component} ({self.component_instance})"
        variable = self.variable_name
        if self.variable_instance:
            variable = f"{variable} ({self.variable_instance})"
        return f"{self.charger}: {component} / {variable}"


class MonitoringRule(Entity):
    """Variable monitoring rules reported by charge points."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="monitoring_rules",
    )
    variable = models.ForeignKey(
        Variable,
        on_delete=models.CASCADE,
        related_name="monitoring_rules",
    )
    monitoring_id = models.BigIntegerField(_("Monitoring ID"))
    severity = models.PositiveIntegerField(_("Severity"), null=True, blank=True)
    monitor_type = models.CharField(_("Type"), max_length=32, blank=True, default="")
    threshold = models.CharField(_("Threshold"), max_length=255, blank=True, default="")
    is_transaction = models.BooleanField(_("Transaction"), default=False)
    is_active = models.BooleanField(_("Active"), default=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_on = models.DateTimeField(auto_now_add=True, verbose_name=_("Created on"))
    updated_on = models.DateTimeField(auto_now=True, verbose_name=_("Updated on"))

    class Meta:
        ordering = ["-updated_on", "monitoring_id", "pk"]
        verbose_name = _("Monitoring Rule")
        verbose_name_plural = _("Monitoring Rules")
        constraints = [
            models.UniqueConstraint(
                fields=["charger", "monitoring_id"],
                name="ocpp_monitoring_rule_unique_id",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.monitoring_id}"


class MonitoringReport(Entity):
    """Monitoring report entries received from charge points."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="monitoring_reports",
    )
    request_id = models.BigIntegerField(_("Request ID"), null=True, blank=True)
    seq_no = models.BigIntegerField(_("Sequence Number"), null=True, blank=True)
    generated_at = models.DateTimeField(
        _("Generated At"),
        null=True,
        blank=True,
    )
    tbc = models.BooleanField(_("To Be Continued"), default=False)
    raw_payload = models.JSONField(default=dict, blank=True)
    reported_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Reported at"))

    class Meta:
        ordering = ["-reported_at", "-pk"]
        verbose_name = _("Monitoring Report")
        verbose_name_plural = _("Monitoring Reports")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.request_id or 'report'}"
