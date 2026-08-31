from __future__ import annotations

from .base import *

class ConfigurationKey(Entity):
    """Single configurationKey entry from a GetConfiguration payload."""

    configuration = models.ForeignKey(
        "ChargerConfiguration",
        on_delete=models.CASCADE,
        related_name="configuration_entries",
    )
    position = models.PositiveIntegerField(default=0)
    key = models.CharField(max_length=255)
    readonly = models.BooleanField(default=False)
    has_value = models.BooleanField(default=False)
    value = models.JSONField(null=True, blank=True)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = _("Configuration Key")
        verbose_name_plural = _("Configuration Keys")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.key

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"key": self.key, "readonly": self.readonly}
        if self.has_value:
            data["value"] = self.value
        if self.extra_data:
            data.update(self.extra_data)
        return data
