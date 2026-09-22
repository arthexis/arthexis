"""Charger, connector, and station-model records."""

from datetime import timedelta
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.ocpp.protocol.contracts import ProtocolVersion


class StationModel(models.Model):
    vendor = models.CharField(max_length=120)
    family = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120)
    preferred_protocol = models.CharField(max_length=20, default="ocpp1.6")
    integration_notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("vendor", "model"), name="unique_station_model"
            )
        ]

    def __str__(self) -> str:
        return f"{self.vendor} {self.model}"


class ChargerQuerySet(models.QuerySet):
    """Composable operational selections for charger fleet state."""

    def enabled(self):
        return self.filter(active=True)

    def disabled(self):
        return self.filter(active=False)

    def connected(self):
        cutoff = timezone.now() - timedelta(seconds=settings.OCPP_PRESENCE_LEASE_SECONDS)
        return self.filter(connection__last_seen_at__gte=cutoff)

    def disconnected(self):
        cutoff = timezone.now() - timedelta(seconds=settings.OCPP_PRESENCE_LEASE_SECONDS)
        return self.exclude(connection__last_seen_at__gte=cutoff)

    def charging(self):
        return self.connected().filter(
            transactions__recovery_state="active",
            transactions__stopped_at__isnull=True,
        ).distinct()

    def unresolved(self):
        return self.filter(
            transactions__recovery_state="unresolved",
            transactions__stopped_at__isnull=True,
        ).distinct()

    def idle(self):
        charging = self.charging().values("pk")
        unresolved = self.unresolved().values("pk")
        return self.connected().exclude(pk__in=charging).exclude(pk__in=unresolved)


class ChargerManager(models.Manager.from_queryset(ChargerQuerySet)):
    """Django-native manager for charger identity and fleet selections."""

    def get_by_natural_key(self, identity: str):
        return self.get(identity=identity)


class Charger(models.Model):
    class AuthorizationMode(models.TextChoices):
        OPEN = "open", "Open"
        RESTRICTED = "restricted", "Restricted"

    objects = ChargerManager()

    identity = models.CharField(max_length=120, unique=True)
    connection_token_hash = models.CharField(max_length=128, blank=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    authorization_mode = models.CharField(
        choices=AuthorizationMode.choices,
        default=AuthorizationMode.OPEN,
        max_length=16,
    )
    station_model = models.ForeignKey(
        StationModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chargers",
    )
    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chargers",
    )
    active = models.BooleanField(default=True)
    connected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.identity

    def natural_key(self) -> tuple[str]:
        return (self.identity,)

    @classmethod
    async def reset(
        cls, charger: "Charger", *, hard: bool = False, timeout: float = 30
    ):
        """Request a graceful or immediate reset for one selected charger."""
        version = await sync_to_async(cls._configured_protocol)(charger)
        payload = (
            {"type": "Hard" if hard else "Soft"}
            if version == ProtocolVersion.OCPP_16
            else {"type": "Immediate" if hard else "OnIdle"}
        )
        return await cls._request_operation(
            charger=charger,
            version=version,
            action="Reset",
            payload=payload,
            timeout=timeout,
        )

    @classmethod
    async def start(
        cls,
        charger: "Charger",
        *,
        id_token: str,
        connector: int | None = None,
        evse: int | None = None,
        timeout: float = 30,
    ):
        """Request a protocol-correct remote start for one selected charger."""
        if not id_token:
            raise ValueError("start requires a non-empty id_token.")

        version = await sync_to_async(cls._configured_protocol)(charger)
        if version == ProtocolVersion.OCPP_16:
            if evse is not None:
                raise ValueError("evse is only available for OCPP 2.0.1 chargers.")
            payload: dict[str, object] = {"idTag": id_token}
            if connector is not None:
                payload["connectorId"] = connector
            action = "RemoteStartTransaction"
        else:
            if connector is not None:
                raise ValueError("connector is only available for OCPP 1.6 chargers.")
            payload = {
                "idToken": {"idToken": id_token},
                "remoteStartId": (uuid4().int % 2_147_483_647) or 1,
            }
            if evse is not None:
                payload["evseId"] = evse
            action = "RequestStartTransaction"

        return await cls._request_operation(
            charger=charger,
            version=version,
            action=action,
            payload=payload,
            timeout=timeout,
        )

    @classmethod
    async def stop(
        cls,
        charger: "Charger",
        *,
        transaction: str | None = None,
        timeout: float = 30,
    ):
        """Request a stop for one selected charger's active transaction."""
        version, selected = await sync_to_async(cls._stop_target)(charger, transaction)

        action = (
            "RemoteStopTransaction"
            if version == ProtocolVersion.OCPP_16
            else "RequestStopTransaction"
        )
        return await cls._request_operation(
            charger=charger,
            version=version,
            action=action,
            payload={"transactionId": selected.remote_id},
            timeout=timeout,
        )

    @staticmethod
    def _configured_protocol(charger: "Charger") -> ProtocolVersion:
        if charger.pk is None:
            raise ValueError("charger operations require a saved charger.")
        if charger.station_model is None:
            raise ValueError(
                f"{charger.identity}: station model has no configured protocol."
            )
        try:
            return ProtocolVersion(charger.station_model.preferred_protocol)
        except ValueError as error:
            raise ValueError(
                f"{charger.identity}: unsupported configured protocol."
            ) from error

    @classmethod
    def _stop_target(
        cls, charger: "Charger", transaction: str | None
    ) -> tuple[ProtocolVersion, object]:
        version = cls._configured_protocol(charger)
        active = charger.transactions.filter(stopped_at__isnull=True)
        if transaction:
            selected = active.filter(remote_id=transaction).first()
            if selected is None:
                raise ValueError(
                    f"{charger.identity}: no active transaction named {transaction}."
                )
            return version, selected

        count = active.count()
        if count == 0:
            raise ValueError(f"{charger.identity}: no active transaction to stop.")
        if count > 1:
            raise ValueError(
                f"{charger.identity}: use transaction to select an active transaction."
            )
        return version, active.get()

    @staticmethod
    async def _request_operation(
        *,
        charger: "Charger",
        version: ProtocolVersion,
        action: str,
        payload: dict[str, object],
        timeout: float,
    ):
        from apps.ocpp.transport.operations import request_explicit_operation

        return await request_explicit_operation(
            charger=charger,
            version=version,
            action=action,
            payload=payload,
            timeout=timeout,
        )


class ChargerConnection(models.Model):
    """The currently connected consumer for one charger."""

    charger = models.OneToOneField(
        Charger,
        on_delete=models.CASCADE,
        related_name="connection",
    )
    channel_name = models.CharField(max_length=255)
    protocol = models.CharField(max_length=12)
    connected_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(default=timezone.now)


class Connector(models.Model):
    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="connectors"
    )
    number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=30, default="available")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("charger", "number"), name="unique_charger_connector"
            )
        ]
