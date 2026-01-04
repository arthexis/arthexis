from .base import *


class DeviceInventorySnapshot(Entity):
    """Device inventory snapshots reported by charge points via NotifyReport."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="device_inventory_snapshots",
    )
    request_id = models.BigIntegerField(_("Request ID"), null=True, blank=True)
    seq_no = models.BigIntegerField(_("Sequence Number"), null=True, blank=True)
    generated_at = models.DateTimeField(_("Generated At"), null=True, blank=True)
    tbc = models.BooleanField(_("To Be Continued"), default=False)
    raw_payload = models.JSONField(default=dict, blank=True)
    reported_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Reported at"))

    class Meta:
        ordering = ["-reported_at", "-pk"]
        verbose_name = _("Device Inventory Snapshot")
        verbose_name_plural = _("Device Inventory Snapshots")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.request_id or 'inventory'}"


class DeviceInventoryItem(Entity):
    """Individual component/variable entries within a device inventory snapshot."""

    snapshot = models.ForeignKey(
        DeviceInventorySnapshot,
        on_delete=models.CASCADE,
        related_name="items",
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
    attributes = models.JSONField(default=list, blank=True)
    characteristics = models.JSONField(default=dict, blank=True)
    reported_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Reported at"))

    class Meta:
        ordering = ["snapshot", "component_name", "variable_name", "pk"]
        verbose_name = _("Device Inventory Item")
        verbose_name_plural = _("Device Inventory Items")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        component = self.component_name
        if self.component_instance:
            component = f"{component} ({self.component_instance})"
        variable = self.variable_name
        if self.variable_instance:
            variable = f"{variable} ({self.variable_instance})"
        return f"{self.snapshot}: {component} / {variable}"
