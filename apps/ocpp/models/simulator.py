from __future__ import annotations

from .base import *
from ..utils import resolve_ws_scheme

class Simulator(Entity):
    """Preconfigured simulator that can be started from the admin."""

    name = models.CharField(max_length=100, unique=True)
    default = models.BooleanField(
        default=False, help_text=_("Mark this simulator as the default option.")
    )
    cp_path = models.CharField(
        _("Serial Number"),
        max_length=100,
    )
    host = models.CharField(max_length=100, default="127.0.0.1")
    ws_port = models.IntegerField(
        _("WS Port"), default=8000, null=True, blank=True
    )
    rfid = models.CharField(
        max_length=255,
        default="FFFFFFFF",
        verbose_name=_("RFID"),
    )
    vin = models.CharField(max_length=17, blank=True)
    serial_number = models.CharField(_("Serial Number"), max_length=100, blank=True)
    connector_id = models.IntegerField(_("Connector ID"), default=1)
    duration = models.IntegerField(default=600)
    interval = models.FloatField(default=5.0)
    pre_charge_delay = models.FloatField(_("Delay"), default=10.0)
    average_kwh = models.FloatField(default=60.0)
    amperage = models.FloatField(default=90.0)
    repeat = models.BooleanField(default=False)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)
    door_open = models.BooleanField(
        _("Door Open"),
        default=False,
        help_text=_("Send a DoorOpen error StatusNotification when enabled."),
    )
    configuration = models.ForeignKey(
        "ChargerConfiguration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="simulators",
        help_text=_("CP Configuration returned for GetConfiguration calls."),
    )
    configuration_keys = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "List of configurationKey entries to return for GetConfiguration calls."
        ),
    )
    configuration_unknown_keys = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Keys to include in the GetConfiguration unknownKey response."),
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def clean(self):
        super().clean()

        # ``save`` enforces that at most one simulator remains marked as the
        # default. Validation is kept lightweight here to allow the admin to
        # promote a new default without having to manually demote the
        # previous one first.

    def validate_constraints(self, exclude=None):
        """Allow promoting a new default without pre-validation conflicts."""

        try:
            super().validate_constraints(exclude=exclude)
        except ValidationError as exc:
            if not self.default:
                raise

            remaining_errors: dict[str, list[str]] = {}
            for field, messages in exc.message_dict.items():
                filtered = [
                    message
                    for message in messages
                    if "unique_default_simulator" not in message
                ]
                if filtered:
                    remaining_errors[field] = filtered

            if remaining_errors:
                raise ValidationError(remaining_errors)

    def save(self, *args, **kwargs):
        if self.default and not self.is_deleted:
            type(self).all_objects.filter(default=True, is_deleted=False).exclude(
                pk=self.pk
            ).update(default=False)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _("CP Simulator")
        verbose_name_plural = _("CP Simulators")
        constraints = [
            models.UniqueConstraint(
                fields=["default"],
                condition=Q(default=True, is_deleted=False),
                name="unique_default_simulator",
            )
        ]

    def as_config(self):
        from ..simulator import SimulatorConfig

        configuration_keys, configuration_unknown_keys = self._configuration_payload()
        ws_scheme = resolve_ws_scheme()

        return SimulatorConfig(
            host=self.host,
            ws_port=self.ws_port,
            ws_scheme=ws_scheme,
            rfid=self.rfid,
            vin=self.vin,
            cp_path=self.cp_path,
            serial_number=self.serial_number,
            connector_id=self.connector_id,
            duration=self.duration,
            interval=self.interval,
            pre_charge_delay=self.pre_charge_delay,
            average_kwh=self.average_kwh,
            amperage=self.amperage,
            repeat=self.repeat,
            username=self.username or None,
            password=self.password or None,
            configuration_keys=configuration_keys,
            configuration_unknown_keys=configuration_unknown_keys,
        )

    def _configuration_payload(self) -> tuple[list[dict[str, object]], list[str]]:
        config_keys: list[dict[str, object]] = []
        unknown_keys: list[str] = []
        if self.configuration_id:
            try:
                configuration = self.configuration
            except ChargerConfiguration.DoesNotExist:  # pragma: no cover - stale FK
                configuration = None
            if configuration:
                config_keys = list(configuration.configuration_keys)
                unknown_keys = list(configuration.unknown_keys or [])
        if not config_keys:
            config_keys = list(self.configuration_keys or [])
        if not unknown_keys:
            unknown_keys = list(self.configuration_unknown_keys or [])
        return config_keys, unknown_keys

    @property
    def ws_url(self) -> str:  # pragma: no cover - simple helper
        path = self.cp_path
        if not path.endswith("/"):
            path += "/"
        scheme = resolve_ws_scheme()
        if self.ws_port:
            return f"{scheme}://{self.host}:{self.ws_port}/{path}"
        return f"{scheme}://{self.host}/{path}"
