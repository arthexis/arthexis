from __future__ import annotations

from .base import *
from .configuration_key import ConfigurationKey

class ChargerConfigurationManager(EntityManager):
    def get_queryset(self):
        return super().get_queryset().prefetch_related("configuration_entries")

class ChargerConfiguration(Entity):
    """Persisted configuration package returned by a charge point."""

    charger_identifier = models.CharField(_("Serial Number"), max_length=100)
    connector_id = models.PositiveIntegerField(
        _("Connector ID"),
        null=True,
        blank=True,
        help_text=_("Connector that returned this configuration (if specified)."),
    )
    unknown_keys = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Keys returned in the unknownKey list."),
    )
    evcs_snapshot_at = models.DateTimeField(
        _("EVCS snapshot at"),
        null=True,
        blank=True,
        help_text=_(
            "Timestamp when this configuration was received from the charge point."
        ),
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Raw payload returned by the GetConfiguration call."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ChargerConfigurationManager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("CP Configuration")
        verbose_name_plural = _("CP Configurations")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        connector = (
            _("connector %(number)s") % {"number": self.connector_id}
            if self.connector_id is not None
            else _("all connectors")
        )
        return _("%(serial)s configuration (%(connector)s)") % {
            "serial": self.charger_identifier,
            "connector": connector,
        }

    @property
    def configuration_keys(self) -> list[dict[str, object]]:
        return [entry.as_dict() for entry in self.configuration_entries.all()]

    def replace_configuration_keys(self, entries: list[dict[str, object]] | None) -> None:
        ConfigurationKey.objects.filter(configuration=self).delete()
        if not entries:
            if hasattr(self, "_prefetched_objects_cache"):
                self._prefetched_objects_cache.pop("configuration_entries", None)
            return

        key_objects: list[ConfigurationKey] = []
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            key_text = str(entry.get("key") or "").strip()
            if not key_text:
                continue
            readonly = bool(entry.get("readonly"))
            has_value = "value" in entry
            value = entry.get("value") if has_value else None
            extras = {
                field_key: field_value
                for field_key, field_value in entry.items()
                if field_key not in {"key", "readonly", "value"}
            }
            key_objects.append(
                ConfigurationKey(
                    configuration=self,
                    position=position,
                    key=key_text,
                    readonly=readonly,
                    has_value=has_value,
                    value=value,
                    extra_data=extras,
                )
            )
        created_keys = ConfigurationKey.objects.bulk_create(key_objects)
        if hasattr(self, "_prefetched_objects_cache"):
            if created_keys:
                refreshed = list(
                    ConfigurationKey.objects.filter(configuration=self)
                    .order_by("position", "id")
                )
                self._prefetched_objects_cache["configuration_entries"] = refreshed
            else:
                self._prefetched_objects_cache.pop("configuration_entries", None)
