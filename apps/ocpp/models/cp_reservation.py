from __future__ import annotations

from .base import *
from .charger import Charger

class CPReservation(Entity):
    """Track connector reservations dispatched to an EVCS."""

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name=_("Location"),
    )
    connector = models.ForeignKey(
        "Charger",
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name=_("Connector"),
    )
    account = models.ForeignKey(
        CustomerAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cp_reservations",
        verbose_name=_("Energy account"),
    )
    rfid = models.ForeignKey(
        CoreRFID,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cp_reservations",
        verbose_name=_("RFID"),
    )
    id_tag = models.CharField(
        _("Id Tag"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Identifier sent to the EVCS when reserving the connector."),
    )
    start_time = models.DateTimeField(verbose_name=_("Start time"))
    duration_minutes = models.PositiveIntegerField(
        verbose_name=_("Duration (minutes)"),
        default=120,
        help_text=_("Reservation window length in minutes."),
    )
    evcs_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("EVCS status"),
    )
    evcs_error = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("EVCS error"),
    )
    evcs_confirmed = models.BooleanField(
        default=False,
        verbose_name=_("Reservation confirmed"),
    )
    evcs_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Confirmed at"),
    )
    ocpp_message_id = models.CharField(
        max_length=36,
        blank=True,
        default="",
        editable=False,
        verbose_name=_("OCPP Message ID"),
    )
    created_on = models.DateTimeField(auto_now_add=True, verbose_name=_("Created on"))
    updated_on = models.DateTimeField(auto_now=True, verbose_name=_("Updated on"))

    class Meta:
        ordering = ["-start_time"]
        verbose_name = _("CP Reservation")
        verbose_name_plural = _("CP Reservations")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        start = timezone.localtime(self.start_time) if self.start_time else ""
        return f"{self.location} @ {start}" if self.location else str(start)

    @property
    def end_time(self):
        duration = max(int(self.duration_minutes or 0), 0)
        return self.start_time + timedelta(minutes=duration)

    @property
    def connector_label(self) -> str:
        if self.connector_id:
            return self.connector.connector_label
        return ""

    @property
    def id_tag_value(self) -> str:
        if self.id_tag:
            return self.id_tag.strip()
        if self.rfid_id:
            return (self.rfid.rfid or "").strip()
        return ""

    def allocate_connector(self, *, force: bool = False) -> Charger:
        """Select an available connector for this reservation."""

        if not self.location_id:
            raise ValidationError({"location": _("Select a location for the reservation.")})
        if not self.start_time:
            raise ValidationError({"start_time": _("Provide a start time for the reservation.")})
        if self.duration_minutes <= 0:
            raise ValidationError(
                {"duration_minutes": _("Reservation window must be at least one minute.")}
            )

        candidates = list(
            Charger.objects.filter(
                location=self.location, connector_id__isnull=False
            ).order_by("connector_id")
        )
        if not candidates:
            raise ValidationError(
                {"location": _("No connectors are configured for the selected location.")}
            )

        def _priority(charger: Charger) -> tuple[int, int]:
            connector_id = charger.connector_id or 0
            if connector_id == 2:
                return (0, connector_id)
            if connector_id == 1:
                return (1, connector_id)
            return (2, connector_id)

        def _is_available(charger: Charger) -> bool:
            existing = type(self).objects.filter(connector=charger).exclude(pk=self.pk)
            start = self.start_time
            end = self.end_time
            for entry in existing:
                if entry.start_time < end and entry.end_time > start:
                    return False
            return True

        if self.connector_id:
            current = next((c for c in candidates if c.pk == self.connector_id), None)
            if current and _is_available(current) and not force:
                return current

        for charger in sorted(candidates, key=_priority):
            if _is_available(charger):
                self.connector = charger
                return charger

        raise ValidationError(
            _("All connectors at this location are reserved for the selected time window.")
        )

    def clean(self):
        super().clean()
        if self.start_time and timezone.is_naive(self.start_time):
            self.start_time = timezone.make_aware(
                self.start_time, timezone.get_current_timezone()
            )
        if self.duration_minutes <= 0:
            raise ValidationError(
                {"duration_minutes": _("Reservation window must be at least one minute.")}
            )
        try:
            self.allocate_connector(force=bool(self.pk))
        except ValidationError as exc:
            raise ValidationError(exc) from exc

    def save(self, *args, **kwargs):
        if self.start_time and timezone.is_naive(self.start_time):
            self.start_time = timezone.make_aware(
                self.start_time, timezone.get_current_timezone()
            )
        update_fields = kwargs.get("update_fields")
        relevant_fields = {"location", "start_time", "duration_minutes", "connector"}
        should_allocate = True
        if update_fields is not None and not relevant_fields.intersection(update_fields):
            should_allocate = False
        if should_allocate:
            self.allocate_connector(force=bool(self.pk))
        super().save(*args, **kwargs)

    def send_reservation_request(self) -> str:
        """Dispatch a ReserveNow request to the associated connector."""

        if not self.pk:
            raise ValidationError(_("Save the reservation before sending it to the EVCS."))
        connector = self.connector
        if connector is None or connector.connector_id is None:
            raise ValidationError(_("Unable to determine which connector to reserve."))
        id_tag = self.id_tag_value
        if not id_tag:
            raise ValidationError(
                _("Provide an RFID or idTag before creating the reservation.")
            )
        connection = store.get_connection(connector.charger_id, connector.connector_id)
        if connection is None:
            raise ValidationError(
                _("The selected charge point is not currently connected to the system.")
            )

        message_id = uuid.uuid4().hex
        expiry = timezone.localtime(self.end_time)
        payload = {
            "connectorId": connector.connector_id,
            "expiryDate": expiry.isoformat(),
            "idTag": id_tag,
            "reservationId": self.pk,
        }
        frame = json.dumps([2, message_id, "ReserveNow", payload])

        log_key = store.identity_key(connector.charger_id, connector.connector_id)
        store.add_log(
            log_key,
            f"ReserveNow request: reservation={self.pk}, expiry={expiry.isoformat()}",
            log_type="charger",
        )
        async_to_sync(connection.send)(frame)

        metadata = {
            "action": "ReserveNow",
            "charger_id": connector.charger_id,
            "connector_id": connector.connector_id,
            "log_key": log_key,
            "reservation_pk": self.pk,
            "requested_at": timezone.now(),
        }
        store.register_pending_call(message_id, metadata)
        store.schedule_call_timeout(message_id, action="ReserveNow", log_key=log_key)

        self.ocpp_message_id = message_id
        self.evcs_status = ""
        self.evcs_error = ""
        self.evcs_confirmed = False
        self.evcs_confirmed_at = None
        super().save(
            update_fields=[
                "ocpp_message_id",
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )
        return message_id

    def send_cancel_request(self) -> str:
        """Dispatch a CancelReservation request for this reservation."""

        if not self.pk:
            raise ValidationError(_("Save the reservation before sending it to the EVCS."))
        connector = self.connector
        if connector is None or connector.connector_id is None:
            raise ValidationError(_("Unable to determine which connector to cancel."))
        connection = store.get_connection(connector.charger_id, connector.connector_id)
        if connection is None:
            raise ValidationError(
                _("The selected charge point is not currently connected to the system.")
            )

        message_id = uuid.uuid4().hex
        payload = {"reservationId": self.pk}
        frame = json.dumps([2, message_id, "CancelReservation", payload])

        log_key = store.identity_key(connector.charger_id, connector.connector_id)
        store.add_log(
            log_key,
            f"CancelReservation request: reservation={self.pk}",
            log_type="charger",
        )
        async_to_sync(connection.send)(frame)

        metadata = {
            "action": "CancelReservation",
            "charger_id": connector.charger_id,
            "connector_id": connector.connector_id,
            "log_key": log_key,
            "reservation_pk": self.pk,
            "requested_at": timezone.now(),
        }
        store.register_pending_call(message_id, metadata)
        store.schedule_call_timeout(
            message_id, action="CancelReservation", log_key=log_key
        )

        self.ocpp_message_id = message_id
        self.evcs_status = ""
        self.evcs_error = ""
        self.evcs_confirmed = False
        self.evcs_confirmed_at = None
        super().save(
            update_fields=[
                "ocpp_message_id",
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )
        return message_id
