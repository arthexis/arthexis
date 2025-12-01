from __future__ import annotations

import binascii
import hashlib
import json
import os
import re
import secrets
import socket
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

from django.apps import apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import models
from django.db.models import DecimalField, OuterRef, Prefetch, Q, Subquery
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from asgiref.sync import async_to_sync

from apps.base.models import Entity, EntityManager
from apps.nodes.models import Node

from apps.energy.models import CustomerAccount, EnergyTariff
from apps.maps.models import Location
from apps.links.models import Reference
from apps.core.models import RFID as CoreRFID, SecurityGroup
from apps.media.models import MediaBucket, MediaFile, media_bucket_slug, media_file_path
from . import store
from apps.links.reference_utils import url_targets_local_loopback


def generate_log_request_id() -> int:
    """Return a random positive identifier suitable for OCPP log requests."""

    import secrets

    # Limit to 31 bits to remain compatible with OCPP integer fields.
    return secrets.randbits(31) or 1


class Charger(Entity):
    """Known charge point."""

    _PLACEHOLDER_SERIAL_RE = re.compile(r"^<[^>]+>$")
    _AUTO_LOCATION_SANITIZE_RE = re.compile(r"[^0-9A-Za-z_-]+")

    class EnergyUnit(models.TextChoices):
        KW = "kW", _("kW")
        W = "W", _("W")

    OPERATIVE_STATUSES = {
        "Available",
        "Preparing",
        "Charging",
        "SuspendedEV",
        "SuspendedEVSE",
        "Finishing",
        "Reserved",
    }
    INOPERATIVE_STATUSES = {"Unavailable", "Faulted"}

    charger_id = models.CharField(
        _("Serial Number"),
        max_length=100,
        help_text="Unique identifier reported by the charger.",
    )
    display_name = models.CharField(
        _("Display Name"),
        max_length=200,
        blank=True,
        help_text="Optional friendly name shown on public pages.",
    )
    connector_id = models.PositiveIntegerField(
        _("Connector ID"),
        blank=True,
        null=True,
        help_text="Optional connector identifier for multi-connector chargers.",
    )
    public_display = models.BooleanField(
        _("Public"),
        default=True,
        help_text="Display this charger on the public status dashboard.",
    )
    language = models.CharField(
        _("Language"),
        max_length=12,
        choices=settings.LANGUAGES,
        default="",
        blank=True,
        help_text=_("Preferred language for the public landing page."),
    )
    preferred_ocpp_version = models.CharField(
        _("Preferred OCPP Version"),
        max_length=16,
        blank=True,
        default="",
        help_text=_(
            "Optional OCPP protocol version to prefer when multiple are available."
        ),
    )
    energy_unit = models.CharField(
        _("Charger Units"),
        max_length=4,
        choices=EnergyUnit.choices,
        default=EnergyUnit.KW,
        help_text=_("Energy unit expected from this charger."),
    )
    require_rfid = models.BooleanField(
        _("Require RFID Authorization"),
        default=False,
        help_text="Require a valid RFID before starting a charging session.",
    )
    firmware_status = models.CharField(
        _("Status"),
        max_length=32,
        blank=True,
        default="",
        help_text="Latest firmware status reported by the charger.",
    )
    firmware_status_info = models.CharField(
        _("Status Details"),
        max_length=255,
        blank=True,
        default="",
        help_text="Additional information supplied with the firmware status.",
    )
    firmware_timestamp = models.DateTimeField(
        _("Status Timestamp"),
        null=True,
        blank=True,
        help_text="When the charger reported the current firmware status.",
    )
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_meter_values = models.JSONField(default=dict, blank=True)
    last_status = models.CharField(max_length=64, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_status_vendor_info = models.JSONField(null=True, blank=True)
    last_status_timestamp = models.DateTimeField(null=True, blank=True)
    availability_state = models.CharField(
        _("State"),
        max_length=16,
        blank=True,
        default="",
        help_text=(
            "Current availability reported by the charger "
            "(Operative/Inoperative)."
        ),
    )
    availability_state_updated_at = models.DateTimeField(
        _("State Updated At"),
        null=True,
        blank=True,
        help_text="When the current availability state became effective.",
    )
    availability_requested_state = models.CharField(
        _("Requested State"),
        max_length=16,
        blank=True,
        default="",
        help_text="Last availability state requested via ChangeAvailability.",
    )
    availability_requested_at = models.DateTimeField(
        _("Requested At"),
        null=True,
        blank=True,
        help_text="When the last ChangeAvailability request was sent.",
    )
    availability_request_status = models.CharField(
        _("Request Status"),
        max_length=16,
        blank=True,
        default="",
        help_text=(
            "Latest response status for ChangeAvailability "
            "(Accepted/Rejected/Scheduled)."
        ),
    )
    availability_request_status_at = models.DateTimeField(
        _("Request Status At"),
        null=True,
        blank=True,
        help_text="When the last ChangeAvailability response was received.",
    )
    availability_request_details = models.CharField(
        _("Request Details"),
        max_length=255,
        blank=True,
        default="",
        help_text="Additional details from the last ChangeAvailability response.",
    )
    temperature = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True
    )
    temperature_unit = models.CharField(max_length=16, blank=True)
    diagnostics_status = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text="Most recent diagnostics status reported by the charger.",
    )
    diagnostics_timestamp = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp associated with the latest diagnostics status.",
    )
    diagnostics_location = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Location or URI reported for the latest diagnostics upload.",
    )
    diagnostics_bucket = models.ForeignKey(
        "media.MediaBucket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chargers",
        help_text=_("Media bucket where this charge point should upload diagnostics."),
    )
    reference = models.OneToOneField(
        Reference, null=True, blank=True, on_delete=models.SET_NULL
    )
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chargers",
    )
    station_model = models.ForeignKey(
        "StationModel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chargers",
        verbose_name=_("Station Model"),
        help_text=_("Optional hardware model for this EVCS."),
    )
    last_path = models.CharField(max_length=255, blank=True)
    configuration = models.ForeignKey(
        "ChargerConfiguration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chargers",
        help_text=_(
            "Latest GetConfiguration response received from this charge point."
        ),
    )
    network_profile = models.ForeignKey(
        "CPNetworkProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chargers",
        help_text=_(
            "Last network profile that was successfully applied to this charge point."
        ),
    )
    local_auth_list_version = models.PositiveIntegerField(
        _("Local list version"),
        null=True,
        blank=True,
        help_text=_("Last RFID list version acknowledged by the charge point."),
    )
    local_auth_list_updated_at = models.DateTimeField(
        _("Local list updated at"),
        null=True,
        blank=True,
        help_text=_("When the charge point reported or accepted the RFID list."),
    )
    node_origin = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="origin_chargers",
    )
    manager_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_chargers",
    )
    forwarded_to = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forwarded_chargers",
        help_text=_("Remote node receiving forwarded transactions."),
    )
    forwarding_watermark = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp of the last forwarded transaction."),
    )
    allow_remote = models.BooleanField(
        default=False,
        help_text=_(
            "Permit this charge point to receive remote commands from its manager "
            "or forwarding target."
        ),
    )
    export_transactions = models.BooleanField(
        default=False,
        help_text=_(
            "Enable to share this charge point's transactions with remote nodes "
            "or export tools. Required for CP forwarders."
        ),
    )
    last_online_at = models.DateTimeField(null=True, blank=True)
    ws_auth_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ws_authenticated_chargers",
        verbose_name=_("WS Auth User"),
        help_text=_(
            "Charge point connections must authenticate with HTTP Basic using this user."
        ),
    )
    ws_auth_group = models.ForeignKey(
        SecurityGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ws_group_authenticated_chargers",
        verbose_name=_("WS Auth Group"),
        help_text=_(
            "Charge point connections must authenticate with HTTP Basic as a member of this security group."
        ),
    )
    owner_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="owned_chargers",
        help_text=_("Users who can view this charge point."),
    )
    owner_groups = models.ManyToManyField(
        SecurityGroup,
        blank=True,
        related_name="owned_chargers",
        help_text=_("Security groups that can view this charge point."),
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.charger_id

    @classmethod
    def visible_for_user(cls, user):
        """Return chargers marked for display that the user may view."""

        qs = cls.objects.filter(public_display=True)
        if getattr(user, "is_superuser", False):
            return qs
        if not getattr(user, "is_authenticated", False):
            return qs.filter(
                owner_users__isnull=True, owner_groups__isnull=True
            ).distinct()
        group_ids = list(user.groups.values_list("pk", flat=True))
        visibility = Q(owner_users__isnull=True, owner_groups__isnull=True) | Q(
            owner_users=user
        )
        if group_ids:
            visibility |= Q(owner_groups__pk__in=group_ids)
        return qs.filter(visibility).distinct()

    def has_owner_scope(self) -> bool:
        """Return ``True`` when owner restrictions are defined."""

        return self.owner_users.exists() or self.owner_groups.exists()

    def is_visible_to(self, user) -> bool:
        """Return ``True`` when ``user`` may view this charger."""

        if getattr(user, "is_superuser", False):
            return True
        if not self.has_owner_scope():
            return True
        if not getattr(user, "is_authenticated", False):
            return False
        if self.owner_users.filter(pk=user.pk).exists():
            return True
        user_group_ids = user.groups.values_list("pk", flat=True)
        return self.owner_groups.filter(pk__in=user_group_ids).exists()

    @property
    def requires_ws_auth(self) -> bool:
        """Return ``True`` when HTTP Basic authentication is required."""

        return bool(self.ws_auth_user_id or self.ws_auth_group_id)

    def is_ws_user_authorized(self, user) -> bool:
        """Return ``True`` when ``user`` satisfies the websocket auth rules."""

        if not self.requires_ws_auth:
            return True
        if self.ws_auth_user_id:
            return getattr(user, "pk", None) == self.ws_auth_user_id
        if self.ws_auth_group_id:
            if getattr(user, "pk", None) is None:
                return False
            groups = getattr(user, "groups", None)
            if groups is None:
                return False
            return groups.filter(pk=self.ws_auth_group_id).exists()
        return False

    @property
    def is_local(self) -> bool:
        """Return ``True`` when this charger originates from the local node."""

        local = Node.get_local()
        if self.node_origin_id is None:
            return True
        if not local:
            return False
        return self.node_origin_id == local.pk

    def save(self, *args, **kwargs):
        if self.node_origin_id is None:
            local = Node.get_local()
            if local:
                self.node_origin = local
        super().save(*args, **kwargs)

    def ensure_diagnostics_bucket(self, *, expires_at: datetime | None = None):
        """Return an active diagnostics bucket, creating one if necessary."""

        bucket = self.diagnostics_bucket
        now = timezone.now()
        if bucket and bucket.is_expired(reference=now):
            bucket = None
        if bucket is None:
            patterns = "\n".join(["*.log", "*.txt", "*.zip", "*.tar", "*.tar.gz"])
            bucket = MediaBucket.objects.create(
                name=f"{self.charger_id} diagnostics".strip(),
                allowed_patterns=patterns,
                expires_at=expires_at,
            )
            Charger.objects.filter(pk=self.pk).update(diagnostics_bucket=bucket)
            self.diagnostics_bucket = bucket
        elif expires_at and (bucket.expires_at is None or bucket.expires_at < expires_at):
            MediaBucket.objects.filter(pk=bucket.pk).update(expires_at=expires_at)
            bucket.expires_at = expires_at
        return bucket

    class Meta:
        verbose_name = _("Charge Point")
        verbose_name_plural = _("Charge Points")
        constraints = [
            models.UniqueConstraint(
                fields=("charger_id", "connector_id"),
                condition=models.Q(connector_id__isnull=False),
                name="charger_connector_unique",
            ),
            models.UniqueConstraint(
                fields=("charger_id",),
                condition=models.Q(connector_id__isnull=True),
                name="charger_unique_without_connector",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ws_auth_user__isnull=True)
                    | models.Q(ws_auth_group__isnull=True)
                ),
                name="charger_ws_auth_user_or_group",
            ),
        ]


    @classmethod
    def normalize_serial(cls, value: str | None) -> str:
        """Return ``value`` trimmed for consistent comparisons."""

        if value is None:
            return ""
        return str(value).strip()

    @classmethod
    def is_placeholder_serial(cls, value: str | None) -> bool:
        """Return ``True`` when ``value`` matches the placeholder pattern."""

        normalized = cls.normalize_serial(value)
        return bool(normalized) and bool(cls._PLACEHOLDER_SERIAL_RE.match(normalized))

    @classmethod
    def validate_serial(cls, value: str | None) -> str:
        """Return a normalized serial number or raise ``ValidationError``."""

        normalized = cls.normalize_serial(value)
        if not normalized:
            raise ValidationError({"charger_id": _("Serial Number cannot be blank.")})
        if cls.is_placeholder_serial(normalized):
            raise ValidationError(
                {
                    "charger_id": _(
                        "Serial Number placeholder values such as <charger_id> are not allowed."
                    )
                }
            )
        return normalized

    def preferred_ocpp_version_value(self) -> str:
        """Return the preferred OCPP version, inheriting from the model when set."""

        if self.preferred_ocpp_version:
            return self.preferred_ocpp_version
        if self.station_model and self.station_model.preferred_ocpp_version:
            return self.station_model.preferred_ocpp_version
        return ""

    @classmethod
    def sanitize_auto_location_name(cls, value: str) -> str:
        """Return a location name containing only safe characters."""

        sanitized = cls._AUTO_LOCATION_SANITIZE_RE.sub("_", value)
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        if not sanitized:
            return "Charger"
        return sanitized

    @staticmethod
    def normalize_energy_value(
        energy: Decimal, unit: str | None, *, default_unit: str | None = None
    ) -> Decimal:
        """Return ``energy`` converted to kWh using the provided units."""

        unit_normalized = (unit or default_unit or Charger.EnergyUnit.KW).lower()
        if unit_normalized in {"w", "wh"}:
            return energy / Decimal("1000")
        return energy

    def convert_energy_to_kwh(self, energy: Decimal, unit: str | None = None) -> Decimal:
        return self.normalize_energy_value(
            energy, unit, default_unit=self.energy_unit
        )

    AGGREGATE_CONNECTOR_SLUG = "all"

    def identity_tuple(self) -> tuple[str, int | None]:
        """Return the canonical identity for this charger."""

        return (
            self.charger_id,
            self.connector_id if self.connector_id is not None else None,
        )

    @classmethod
    def connector_slug_from_value(cls, connector: int | None) -> str:
        """Return the slug used in URLs for the given connector."""

        return cls.AGGREGATE_CONNECTOR_SLUG if connector is None else str(connector)

    @classmethod
    def connector_value_from_slug(cls, slug: int | str | None) -> int | None:
        """Return the connector integer represented by ``slug``."""

        if slug in (None, "", cls.AGGREGATE_CONNECTOR_SLUG):
            return None
        if isinstance(slug, int):
            return slug
        try:
            return int(str(slug))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid connector slug: {slug}") from exc

    @classmethod
    def connector_letter_from_value(cls, connector: int | str | None) -> str | None:
        """Return the alphabetical label associated with ``connector``."""

        if connector in (None, "", cls.AGGREGATE_CONNECTOR_SLUG):
            return None
        try:
            value = int(connector)
        except (TypeError, ValueError):
            text = str(connector).strip()
            return text or None
        if value <= 0:
            return str(value)

        letters: list[str] = []
        while value > 0:
            value -= 1
            letters.append(chr(ord("A") + (value % 26)))
            value //= 26
        return "".join(reversed(letters))

    @classmethod
    def connector_letter_from_slug(cls, slug: int | str | None) -> str | None:
        """Return the alphabetical label represented by ``slug``."""

        value = cls.connector_value_from_slug(slug)
        return cls.connector_letter_from_value(value)

    @classmethod
    def connector_value_from_letter(cls, label: str) -> int:
        """Return the connector integer represented by an alphabetical label."""

        normalized = (label or "").strip().upper()
        if not normalized:
            raise ValueError("Connector label is required")

        total = 0
        for char in normalized:
            if not ("A" <= char <= "Z"):
                raise ValueError(f"Invalid connector label: {label}")
            total = total * 26 + (ord(char) - ord("A") + 1)
        return total

    @classmethod
    def availability_state_from_status(cls, status: str) -> str | None:
        """Return the availability state implied by a status notification."""

        normalized = (status or "").strip()
        if not normalized:
            return None
        if normalized in cls.INOPERATIVE_STATUSES:
            return "Inoperative"
        if normalized in cls.OPERATIVE_STATUSES:
            return "Operative"
        return None

    @property
    def connector_slug(self) -> str:
        """Return the slug representing this charger's connector."""

        return type(self).connector_slug_from_value(self.connector_id)

    @property
    def connector_letter(self) -> str | None:
        """Return the alphabetical identifier for this connector."""

        return type(self).connector_letter_from_value(self.connector_id)

    @property
    def connector_label(self) -> str:
        """Return a short human readable label for this connector."""

        if self.connector_id is None:
            return _("All Connectors")

        letter = self.connector_letter or str(self.connector_id)
        if self.connector_id == 1:
            side = _("Left")
            return _("Connector %(letter)s (%(side)s)") % {
                "letter": letter,
                "side": side,
            }
        if self.connector_id == 2:
            side = _("Right")
            return _("Connector %(letter)s (%(side)s)") % {
                "letter": letter,
                "side": side,
            }

        return _("Connector %(letter)s") % {"letter": letter}

    def identity_slug(self) -> str:
        """Return a unique slug for this charger identity."""

        serial, connector = self.identity_tuple()
        return f"{serial}#{type(self).connector_slug_from_value(connector)}"

    def get_absolute_url(self):
        serial, connector = self.identity_tuple()
        connector_slug = type(self).connector_slug_from_value(connector)
        if connector_slug == self.AGGREGATE_CONNECTOR_SLUG:
            return reverse("charger-page", args=[serial])
        return reverse("charger-page-connector", args=[serial, connector_slug])

    def _fallback_domain(self) -> str:
        """Return a best-effort hostname when the Sites framework is unset."""

        fallback = getattr(settings, "DEFAULT_SITE_DOMAIN", "") or getattr(
            settings, "DEFAULT_DOMAIN", ""
        )
        if fallback:
            return fallback.strip()

        for host in getattr(settings, "ALLOWED_HOSTS", []):
            if not isinstance(host, str):
                continue
            host = host.strip()
            if not host or host.startswith("*") or "/" in host:
                continue
            return host

        return socket.gethostname() or "localhost"

    def _full_url(self) -> str:
        """Return absolute URL for the charger landing page."""

        try:
            domain = Site.objects.get_current().domain.strip()
        except Site.DoesNotExist:
            domain = ""

        if not domain:
            domain = self._fallback_domain()

        scheme = getattr(settings, "DEFAULT_HTTP_PROTOCOL", "http")
        return f"{scheme}://{domain}{self.get_absolute_url()}"

    def clean(self):
        super().clean()
        self.charger_id = type(self).validate_serial(self.charger_id)
        if self.ws_auth_user_id and self.ws_auth_group_id:
            raise ValidationError(
                {
                    "ws_auth_user": _(
                        "Select either a WS Auth User or WS Auth Group, not both."
                    ),
                    "ws_auth_group": _(
                        "Select either a WS Auth User or WS Auth Group, not both."
                    ),
                }
            )

    def save(self, *args, **kwargs):
        self.clean()
        update_fields = kwargs.get("update_fields")
        update_list = list(update_fields) if update_fields is not None else None
        if not self.manager_node_id:
            local_node = Node.get_local()
            if local_node:
                self.manager_node = local_node
                if update_list is not None and "manager_node" not in update_list:
                    update_list.append("manager_node")
        if not self.location_id:
            existing = (
                type(self)
                .objects.filter(charger_id=self.charger_id, location__isnull=False)
                .exclude(pk=self.pk)
                .select_related("location")
                .first()
            )
            if existing:
                self.location = existing.location
            else:
                auto_name = type(self).sanitize_auto_location_name(self.charger_id)
                location, _ = Location.objects.get_or_create(name=auto_name)
                self.location = location
            if update_list is not None and "location" not in update_list:
                update_list.append("location")
        if update_list is not None:
            kwargs["update_fields"] = update_list
        super().save(*args, **kwargs)
        ref_value = self._full_url()
        if url_targets_local_loopback(ref_value):
            return
        if not self.reference:
            self.reference = Reference.objects.create(
                value=ref_value, alt_text=self.charger_id
            )
            super().save(update_fields=["reference"])
        elif self.reference.value != ref_value:
            Reference.objects.filter(pk=self.reference_id).update(
                value=ref_value, alt_text=self.charger_id
            )
            self.reference.value = ref_value
            self.reference.alt_text = self.charger_id

    def refresh_manager_node(self, node: Node | None = None) -> Node | None:
        """Ensure ``manager_node`` matches the provided or local node."""

        node = node or Node.get_local()
        if not node:
            return None
        if self.pk is None:
            self.manager_node = node
            return node
        if self.manager_node_id != node.pk:
            type(self).objects.filter(pk=self.pk).update(manager_node=node)
            self.manager_node = node
        return node

    @property
    def name(self) -> str:
        if self.location:
            if self.connector_id is not None:
                return f"{self.location.name} #{self.connector_id}"
            return self.location.name
        return ""

    @property
    def latitude(self):
        return self.location.latitude if self.location else None

    @property
    def longitude(self):
        return self.location.longitude if self.location else None

    @property
    def total_kw(self) -> float:
        """Return total energy delivered by this charger in kW."""
        from . import store

        total = 0.0
        for charger in self._target_chargers():
            total += charger._total_kw_single(store)
        return total

    def _store_keys(self) -> list[str]:
        """Return keys used for store lookups with fallbacks."""

        from . import store

        base = self.charger_id
        connector = self.connector_id
        keys: list[str] = []
        keys.append(store.identity_key(base, connector))
        if connector is not None:
            keys.append(store.identity_key(base, None))
        keys.append(store.pending_key(base))
        keys.append(base)
        seen: set[str] = set()
        deduped: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _target_chargers(self):
        """Return chargers contributing to aggregate operations."""

        qs = type(self).objects.filter(charger_id=self.charger_id)
        if self.connector_id is None:
            return qs
        return qs.filter(pk=self.pk)

    def total_kw_for_range(
        self,
        start=None,
        end=None,
    ) -> float:
        """Return total energy delivered within ``start``/``end`` window."""

        from . import store

        total = 0.0
        for charger in self._target_chargers():
            total += charger._total_kw_range_single(store, start, end)
        return total

    def _total_kw_single(self, store_module) -> float:
        """Return total kW for this specific charger identity."""

        return self._total_kw_range_single(store_module)

    def _total_kw_range_single(self, store_module, start=None, end=None) -> float:
        """Return total kW for a date range for this charger."""

        tx_active = None
        if self.connector_id is not None:
            tx_active = store_module.get_transaction(self.charger_id, self.connector_id)

        qs = self.transactions.all()
        if start is not None:
            qs = qs.filter(start_time__gte=start)
        if end is not None:
            qs = qs.filter(start_time__lt=end)
        if tx_active and tx_active.pk is not None:
            qs = qs.exclude(pk=tx_active.pk)
        qs = annotate_transaction_energy_bounds(qs)

        total = 0.0
        for tx in qs.iterator():
            kw = tx.kw
            if kw:
                total += kw

        if tx_active:
            start_time = getattr(tx_active, "start_time", None)
            include = True
            if start is not None and start_time and start_time < start:
                include = False
            if end is not None and start_time and start_time >= end:
                include = False
            if include:
                kw = tx_active.kw
                if kw:
                    total += kw
        return total

    def purge(self):
        from . import store

        for charger in self._target_chargers():
            charger.transactions.all().delete()
            charger.meter_values.all().delete()
            for key in charger._store_keys():
                store.clear_log(key, log_type="charger")
                store.transactions.pop(key, None)
                store.history.pop(key, None)

    def delete(self, *args, **kwargs):
        from django.db.models.deletion import ProtectedError
        from . import store

        for charger in self._target_chargers():
            has_db_data = charger.transactions.exists() or charger.meter_values.exists()
            has_store_data = (
                any(
                    store.get_logs(key, log_type="charger")
                    for key in charger._store_keys()
                )
                or any(store.transactions.get(key) for key in charger._store_keys())
                or any(store.history.get(key) for key in charger._store_keys())
            )
            if has_db_data:
                raise ProtectedError("Purge data before deleting charger.", [])

            if has_store_data:
                charger.purge()
        super().delete(*args, **kwargs)


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


class CPNetworkProfile(Entity):
    """Network profile that can be provisioned via SetNetworkProfile."""

    name = models.CharField(_("Name"), max_length=200)
    description = models.TextField(_("Description"), blank=True)
    configuration_slot = models.PositiveIntegerField(
        _("Configuration slot"), default=1, help_text=_("Target configurationSlot.")
    )
    connection_data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("connectionData payload for SetNetworkProfile."),
    )
    apn = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Optional APN settings to include with the profile."),
    )
    vpn = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Optional VPN settings to include with the profile."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "configuration_slot"]
        verbose_name = _("CP Network Profile")
        verbose_name_plural = _("CP Network Profiles")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def build_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "configurationSlot": self.configuration_slot,
            "connectionData": self.connection_data or {},
        }
        if self.apn:
            payload["apn"] = self.apn
        if self.vpn:
            payload["vpn"] = self.vpn
        return payload


class CPNetworkProfileDeployment(Entity):
    """Track SetNetworkProfile deployments for specific charge points."""

    network_profile = models.ForeignKey(
        CPNetworkProfile,
        on_delete=models.CASCADE,
        related_name="deployments",
        verbose_name=_("Network profile"),
    )
    charger = models.ForeignKey(
        "Charger",
        on_delete=models.PROTECT,
        related_name="network_profile_deployments",
        verbose_name=_("Charge point"),
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="network_profile_deployments",
        verbose_name=_("Node"),
    )
    ocpp_message_id = models.CharField(
        _("OCPP message ID"), max_length=64, blank=True
    )
    status = models.CharField(_("Status"), max_length=32, blank=True)
    status_info = models.CharField(_("Status details"), max_length=255, blank=True)
    status_timestamp = models.DateTimeField(_("Status timestamp"), null=True, blank=True)
    requested_at = models.DateTimeField(_("Requested at"), auto_now_add=True)
    completed_at = models.DateTimeField(_("Completed at"), null=True, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("CP Network Profile Deployment")
        verbose_name_plural = _("CP Network Profile Deployments")
        indexes = [
            models.Index(fields=["ocpp_message_id"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.pk:
            return f"{self.network_profile} → {self.charger}"
        return "Network profile deployment"

    def mark_status(
        self,
        status_value: str,
        status_info: str = "",
        timestamp: datetime | None = None,
        *,
        response: dict | None = None,
    ) -> None:
        if timestamp is None:
            timestamp = timezone.now()
        updates = {
            "status": status_value,
            "status_info": status_info,
            "status_timestamp": timestamp,
        }
        if response is not None:
            updates["response_payload"] = response
        CPNetworkProfileDeployment.objects.filter(pk=self.pk).update(**updates)
        for field, value in updates.items():
            setattr(self, field, value)


class ChargingProfile(Entity):
    """Charging profiles dispatched through SetChargingProfile."""

    class Purpose(models.TextChoices):
        CHARGE_POINT_MAX_PROFILE = "ChargePointMaxProfile", _(
            "Charge Point Max Profile"
        )
        TX_DEFAULT_PROFILE = "TxDefaultProfile", _("Transaction Default Profile")
        TX_PROFILE = "TxProfile", _("Transaction Profile")

    class Kind(models.TextChoices):
        ABSOLUTE = "Absolute", _("Absolute")
        RECURRING = "Recurring", _("Recurring")
        RELATIVE = "Relative", _("Relative")

    class RecurrencyKind(models.TextChoices):
        DAILY = "Daily", _("Daily")
        WEEKLY = "Weekly", _("Weekly")

    class RateUnit(models.TextChoices):
        AMP = "A", _("Amperes")
        WATT = "W", _("Watts")

    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        related_name="charging_profiles",
        verbose_name=_("Charger"),
        null=True,
        blank=True,
        help_text=_("Optional default charger context for the profile."),
    )
    connector_id = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Connector ID"),
        help_text=_(
            "Connector targeted by this profile (0 sends the profile to the EVCS)."
        ),
    )
    charging_profile_id = models.PositiveIntegerField(
        verbose_name=_("Charging Profile ID"),
        help_text=_("Identifier sent as chargingProfileId in OCPP payloads."),
    )
    stack_level = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Stack Level"),
        help_text=_("Priority applied when multiple profiles overlap."),
    )
    purpose = models.CharField(
        max_length=32,
        choices=Purpose.choices,
        verbose_name=_("Purpose"),
    )
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        verbose_name=_("Profile Kind"),
    )
    recurrency_kind = models.CharField(
        max_length=16,
        choices=RecurrencyKind.choices,
        blank=True,
        default="",
        verbose_name=_("Recurrency Kind"),
    )
    transaction_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Transaction ID"),
        help_text=_("Required when Purpose is TxProfile."),
    )
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    last_status = models.CharField(max_length=32, blank=True, default="")
    last_status_info = models.CharField(max_length=255, blank=True, default="")
    last_response_payload = models.JSONField(default=dict, blank=True)
    last_response_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "charger__charger_id",
            "connector_id",
            "-stack_level",
            "charging_profile_id",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["charger", "connector_id", "charging_profile_id"],
                name="unique_charging_profile_per_connector",
                condition=Q(charger__isnull=False),
            )
        ]
        verbose_name = _("Charging Profile")
        verbose_name_plural = _("Charging Profiles")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        connector = self.connector_id or 0
        return (
            f"{self.charger or 'Unassigned'} | {connector} | "
            f"{self.charging_profile_id}"
        )

    def clean(self):
        super().clean()

        errors: dict[str, list[str]] = {}

        if self.recurrency_kind and self.kind != self.Kind.RECURRING:
            errors.setdefault("recurrency_kind", []).append(
                _("Recurrency kind is only valid for recurring profiles.")
            )
        if self.kind == self.Kind.RECURRING and not self.recurrency_kind:
            errors.setdefault("recurrency_kind", []).append(
                _("Recurring profiles must define a recurrency kind.")
            )
        if self.purpose == self.Purpose.TX_PROFILE and not self.transaction_id:
            errors.setdefault("transaction_id", []).append(
                _("Transaction ID is required for TxProfile entries.")
            )

        if self.pk and not getattr(self, "schedule", None):
            errors.setdefault("schedule", []).append(
                _("Each charging profile must include a schedule.")
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def _format_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if timezone.is_naive(value):
            return value.isoformat()
        return timezone.localtime(value).isoformat()

    def _schedule_payload(self) -> dict[str, object]:
        if not getattr(self, "schedule", None):
            return {}
        return self.schedule.as_charging_schedule_payload()

    def as_cs_charging_profile(
        self, *, schedule_payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chargingProfileId": self.charging_profile_id,
            "stackLevel": self.stack_level,
            "chargingProfilePurpose": self.purpose,
            "chargingProfileKind": self.kind,
            "chargingSchedule": schedule_payload or self._schedule_payload(),
        }

        if self.transaction_id:
            payload["transactionId"] = self.transaction_id
        if self.recurrency_kind:
            payload["recurrencyKind"] = self.recurrency_kind
        if self.valid_from:
            payload["validFrom"] = self._format_datetime(self.valid_from)
        if self.valid_to:
            payload["validTo"] = self._format_datetime(self.valid_to)

        return payload

    def as_set_charging_profile_request(
        self,
        *,
        connector_id: int | None = None,
        schedule_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        connector_value = self.connector_id if connector_id is None else connector_id
        return {
            "connectorId": connector_value,
            "csChargingProfiles": self.as_cs_charging_profile(
                schedule_payload=schedule_payload
            ),
        }

    def matches_clear_filter(
        self,
        *,
        profile_id: int | None = None,
        connector_id: int | None = None,
        purpose: str | None = None,
        stack_level: int | None = None,
    ) -> bool:
        if profile_id is not None and self.charging_profile_id != profile_id:
            return False
        if connector_id is not None and self.connector_id != connector_id:
            return False
        if purpose is not None and self.purpose != purpose:
            return False
        if stack_level is not None and self.stack_level != stack_level:
            return False
        return True


class ChargingSchedule(Entity):
    """Charging schedule linked to a :class:`ChargingProfile`."""

    profile = models.OneToOneField(
        ChargingProfile,
        on_delete=models.CASCADE,
        related_name="schedule",
    )
    start_schedule = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Optional schedule start time; defaults to immediate execution."),
    )
    duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Duration (seconds)"),
    )
    charging_rate_unit = models.CharField(
        max_length=2,
        choices=ChargingProfile.RateUnit.choices,
        verbose_name=_("Charging Rate Unit"),
    )
    charging_schedule_periods = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "List of schedule periods including start_period, limit, "
            "number_phases, and phase_to_use values."
        ),
    )
    min_charging_rate = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name=_("Minimum Charging Rate"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Charging Schedule")
        verbose_name_plural = _("Charging Schedules")
        ordering = ["profile_id"]

    @staticmethod
    def _coerce_decimal(value: object) -> Decimal | None:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _normalize_schedule_periods(self) -> tuple[list[dict[str, object]], list[str]]:
        normalized: list[dict[str, object]] = []
        errors: list[str] = []
        raw_periods = self.charging_schedule_periods or []

        for index, period in enumerate(raw_periods, start=1):
            if not isinstance(period, dict):
                errors.append(
                    _("Period %(index)s must be a mapping." % {"index": index})
                )
                continue

            start_period = period.get("start_period", period.get("startPeriod"))
            limit_value = period.get("limit")
            number_phases = period.get("number_phases", period.get("numberPhases"))
            phase_to_use = period.get("phase_to_use", period.get("phaseToUse"))

            try:
                start_int = int(start_period)
            except (TypeError, ValueError):
                errors.append(
                    _(
                        "Period %(index)s is missing a valid start_period."
                        % {"index": index}
                    )
                )
                continue

            decimal_limit = self._coerce_decimal(limit_value)
            if decimal_limit is None or decimal_limit <= 0:
                errors.append(
                    _(
                        "Period %(index)s is missing a positive charging limit."
                        % {"index": index}
                    )
                )
                continue

            entry: dict[str, object] = {
                "start_period": start_int,
                "limit": float(decimal_limit),
            }

            try:
                if number_phases not in {None, ""}:
                    phases_int = int(number_phases)
                    if phases_int in {1, 3}:
                        entry["number_phases"] = phases_int
                    else:
                        errors.append(
                            _(
                                "Period %(index)s number_phases must be 1 or 3."
                                % {"index": index}
                            )
                        )
                        continue
            except (TypeError, ValueError):
                errors.append(
                    _(
                        "Period %(index)s has an invalid number_phases value."
                        % {"index": index}
                    )
                )
                continue

            try:
                if phase_to_use not in {None, ""}:
                    phase_int = int(phase_to_use)
                    entry["phase_to_use"] = phase_int
            except (TypeError, ValueError):
                errors.append(
                    _(
                        "Period %(index)s has an invalid phase_to_use value."
                        % {"index": index}
                    )
                )
                continue

            normalized.append(entry)

        normalized.sort(key=lambda entry: entry["start_period"])
        return normalized, errors

    def clean_fields(self, exclude=None):
        exclude = exclude or []
        normalized_periods, period_errors = self._normalize_schedule_periods()
        self.charging_schedule_periods = normalized_periods

        try:
            super().clean_fields(exclude=exclude)
        except ValidationError as exc:
            errors = exc.error_dict
        else:
            errors: dict[str, list[str]] = {}

        if period_errors:
            errors.setdefault("charging_schedule_periods", []).extend(period_errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()

        errors: dict[str, list[str]] = {}

        if self.duration_seconds is not None and self.duration_seconds <= 0:
            errors.setdefault("duration_seconds", []).append(
                _("Duration must be greater than zero when provided.")
            )
        if self.min_charging_rate is not None and self.min_charging_rate <= 0:
            errors.setdefault("min_charging_rate", []).append(
                _("Minimum charging rate must be positive when provided.")
            )
        if not self.charging_schedule_periods:
            errors.setdefault("charging_schedule_periods", []).append(
                _("Provide at least one charging schedule period.")
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def _format_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if timezone.is_naive(value):
            return value.isoformat()
        return timezone.localtime(value).isoformat()

    def as_charging_schedule_payload(
        self, *, periods: list[dict[str, object]] | None = None
    ) -> dict[str, object]:
        schedule: dict[str, object] = {
            "chargingRateUnit": self.charging_rate_unit,
            "chargingSchedulePeriod": [
                {
                    "startPeriod": period["start_period"],
                    "limit": period["limit"],
                    **(
                        {"numberPhases": period["number_phases"]}
                        if "number_phases" in period
                        else {}
                    ),
                    **(
                        {"phaseToUse": period["phase_to_use"]}
                        if "phase_to_use" in period
                        else {}
                    ),
                }
                for period in (periods if periods is not None else self.charging_schedule_periods)
            ],
        }

        if self.duration_seconds is not None:
            schedule["duration"] = self.duration_seconds
        if self.start_schedule:
            schedule["startSchedule"] = self._format_datetime(self.start_schedule)
        if self.min_charging_rate is not None:
            schedule["minChargingRate"] = float(self.min_charging_rate)

        return schedule


class ChargingProfileDispatch(Entity):
    """Track where a charging profile has been dispatched."""

    profile = models.ForeignKey(
        ChargingProfile,
        on_delete=models.CASCADE,
        related_name="dispatches",
    )
    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        related_name="charging_profile_dispatches",
    )
    message_id = models.CharField(max_length=36, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    status_info = models.CharField(max_length=255, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Charging Profile Dispatch")
        verbose_name_plural = _("Charging Profile Dispatches")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "charger", "message_id"],
                name="unique_charging_profile_dispatch_message",
            )
        ]

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.profile} -> {self.charger}" if self.charger else str(self.profile)


class PowerProjection(Entity):
    """Aggregated power schedules returned by GetCompositeSchedule."""

    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        related_name="power_projections",
        verbose_name=_("Charger"),
    )
    connector_id = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Connector ID"),
        help_text=_("Connector targeted by the projection (0 for the EVCS)."),
    )
    duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Duration (seconds)"),
    )
    charging_rate_unit = models.CharField(
        max_length=2,
        choices=ChargingProfile.RateUnit.choices,
        blank=True,
        default="",
        verbose_name=_("Charging rate unit"),
    )
    charging_schedule_periods = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Composite schedule periods returned by the EVCS."),
    )
    schedule_start = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Schedule start"),
    )
    status = models.CharField(max_length=32, blank=True, default="")
    raw_response = models.JSONField(default=dict, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-requested_at", "-pk"]
        verbose_name = _("Power Projection")
        verbose_name_plural = _("Power Projections")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        connector = self.connector_id or 0
        return f"{self.charger} [{connector}] projection"

class Transaction(Entity):
    """Charging session data stored for each charger."""

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="transactions", null=True
    )
    account = models.ForeignKey(
        CustomerAccount, on_delete=models.PROTECT, related_name="transactions", null=True
    )
    rfid = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("RFID"),
    )
    vid = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("VID"),
        help_text=_("Vehicle identifier reported by the charger."),
    )
    vin = models.CharField(
        max_length=17,
        blank=True,
        help_text=_("Deprecated. Use VID instead."),
    )
    ocpp_transaction_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=_(
            "Transaction identifier reported by the charge point or generated by OCPP."
        ),
        verbose_name=_("OCPP Transaction ID"),
    )
    connector_id = models.PositiveIntegerField(null=True, blank=True)
    meter_start = models.IntegerField(null=True, blank=True)
    meter_stop = models.IntegerField(null=True, blank=True)
    voltage_start = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    voltage_stop = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_import_start = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_import_stop = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_offered_start = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_offered_stop = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    temperature_start = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    temperature_stop = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    soc_start = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    soc_stop = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    start_time = models.DateTimeField()
    stop_time = models.DateTimeField(null=True, blank=True)
    received_start_time = models.DateTimeField(null=True, blank=True)
    received_stop_time = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}:{self.pk}"

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("CP Transactions")

    @property
    def vehicle_identifier(self) -> str:
        """Return the preferred vehicle identifier for this transaction."""

        vid = (self.vid or "").strip()
        if vid:
            return vid

        return (self.vin or "").strip()

    @property
    def vehicle_identifier_source(self) -> str:
        """Return which field supplies :pyattr:`vehicle_identifier`."""

        if (self.vid or "").strip():
            return "vid"
        if (self.vin or "").strip():
            return "vin"
        return ""

    @property
    def kw(self) -> float:
        """Return consumed energy in kW for this session."""
        start_val = None
        if self.meter_start is not None:
            start_val = float(self.meter_start) / 1000.0

        end_val = None
        if self.meter_stop is not None:
            end_val = float(self.meter_stop) / 1000.0

        def _coerce(value):
            if value in {None, ""}:
                return None
            try:
                return float(value)
            except (TypeError, ValueError, InvalidOperation):
                return None

        if start_val is None:
            annotated_start = getattr(self, "meter_energy_start", None)
            if annotated_start is None:
                annotated_start = getattr(self, "report_meter_energy_start", None)
            start_val = _coerce(annotated_start)

        if end_val is None:
            annotated_end = getattr(self, "meter_energy_end", None)
            if annotated_end is None:
                annotated_end = getattr(self, "report_meter_energy_end", None)
            end_val = _coerce(annotated_end)

        readings: list[MeterValue] | None = None
        if start_val is None or end_val is None:
            if hasattr(self, "prefetched_meter_values"):
                readings = [
                    reading
                    for reading in getattr(self, "prefetched_meter_values")
                    if getattr(reading, "energy", None) is not None
                ]
            else:
                cache = getattr(self, "_prefetched_objects_cache", None)
                if cache and "meter_values" in cache:
                    readings = [
                        reading
                        for reading in cache["meter_values"]
                        if getattr(reading, "energy", None) is not None
                    ]

            if readings is not None:
                readings.sort(key=lambda reading: reading.timestamp)

        if readings is not None and readings:
            if start_val is None:
                start_val = _coerce(readings[0].energy)
            if end_val is None:
                end_val = _coerce(readings[-1].energy)
        elif start_val is None or end_val is None:
            readings_qs = self.meter_values.filter(energy__isnull=False).order_by(
                "timestamp"
            )
            if start_val is None:
                first_energy = readings_qs.values_list("energy", flat=True).first()
                start_val = _coerce(first_energy)
            if end_val is None:
                last_energy = readings_qs.order_by("-timestamp").values_list(
                    "energy", flat=True
                ).first()
                end_val = _coerce(last_energy)

        if start_val is None or end_val is None:
            return 0.0

        total = end_val - start_val
        return max(total, 0.0)


class RFIDSessionAttempt(Entity):
    """Record RFID authorisation attempts received via StartTransaction."""

    class Status(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Rejected")

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="rfid_attempts",
        null=True,
        blank=True,
    )
    rfid = models.CharField(_("RFID"), max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    attempted_at = models.DateTimeField(auto_now_add=True)
    account = models.ForeignKey(
        CustomerAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rfid_attempts",
    )
    transaction = models.ForeignKey(
        "Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rfid_attempts",
    )

    class Meta:
        ordering = ["-attempted_at"]
        verbose_name = _("RFID Session Attempt")
        verbose_name_plural = _("RFID Session Attempts")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        status = self.get_status_display() or ""
        tag = self.rfid or "-"
        return f"{tag} ({status})"


class SecurityEvent(Entity):
    """Security-related events reported by a charge point."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="security_events",
    )
    event_type = models.CharField(_("Event Type"), max_length=120)
    event_timestamp = models.DateTimeField(_("Event Timestamp"))
    trigger = models.CharField(max_length=120, blank=True, default="")
    tech_info = models.TextField(blank=True, default="")
    sequence_number = models.BigIntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_timestamp", "-pk"]
        verbose_name = _("Security Event")
        verbose_name_plural = _("Security Events")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.event_type}"


class ChargerLogRequest(Entity):
    """Track GetLog interactions initiated against a charge point."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="log_requests",
    )
    request_id = models.BigIntegerField(
        _("Request Id"), default=generate_log_request_id, unique=True
    )
    message_id = models.CharField(max_length=64, blank=True, default="")
    log_type = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    filename = models.CharField(max_length=255, blank=True, default="")
    location = models.CharField(max_length=500, blank=True, default="")
    session_key = models.CharField(max_length=200, blank=True, default="")
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    last_status_at = models.DateTimeField(null=True, blank=True)
    last_status_payload = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        verbose_name = _("Log Request")
        verbose_name_plural = _("Log Requests")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.log_type or "Log"
        return f"{label} request {self.request_id}"


class MeterValue(Entity):
    """Parsed meter values reported by chargers."""

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="meter_values"
    )
    connector_id = models.PositiveIntegerField(null=True, blank=True)
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="meter_values",
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField()
    context = models.CharField(max_length=32, blank=True)
    energy = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    voltage = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_import = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_offered = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    temperature = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    soc = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger} {self.timestamp}"

    @property
    def value(self):
        return self.energy

    @value.setter
    def value(self, new_value):
        self.energy = new_value

    class Meta:
        verbose_name = _("Meter Value")
        verbose_name_plural = _("Meter Values")


class MeterReadingManager(EntityManager):
    def _normalize_kwargs(self, kwargs: dict) -> dict:
        normalized = dict(kwargs)
        value = normalized.pop("value", None)
        unit = normalized.pop("unit", None)
        charger = normalized.get("charger")
        charger_unit = getattr(charger, "energy_unit", None)
        if value is not None:
            energy = value
            try:
                energy = Decimal(value)
            except (InvalidOperation, TypeError, ValueError):
                energy = None
            if energy is not None:
                normalized.setdefault(
                    "energy",
                    Charger.normalize_energy_value(
                        energy, unit, default_unit=charger_unit
                    ),
                )
        normalized.pop("measurand", None)
        return normalized

    def create(self, **kwargs):
        return super().create(**self._normalize_kwargs(kwargs))

    def get_or_create(self, defaults=None, **kwargs):
        if defaults:
            defaults = self._normalize_kwargs(defaults)
        return super().get_or_create(
            defaults=defaults, **self._normalize_kwargs(kwargs)
        )


class MeterReading(MeterValue):
    """Proxy model for backwards compatibility."""

    objects = MeterReadingManager()

    class Meta:
        proxy = True
        verbose_name = _("Meter Value")
        verbose_name_plural = _("Meter Values")


def annotate_transaction_energy_bounds(
    queryset, *, start_field: str = "meter_energy_start", end_field: str = "meter_energy_end"
):
    """Annotate transactions with their earliest and latest energy readings."""

    energy_qs = MeterValue.objects.filter(
        transaction=OuterRef("pk"), energy__isnull=False
    )
    start_subquery = energy_qs.order_by("timestamp").values("energy")[:1]
    end_subquery = energy_qs.order_by("-timestamp").values("energy")[:1]

    annotations = {
        start_field: Subquery(
            start_subquery, output_field=DecimalField(max_digits=12, decimal_places=3)
        ),
        end_field: Subquery(
            end_subquery, output_field=DecimalField(max_digits=12, decimal_places=3)
        ),
    }
    return queryset.annotate(**annotations)


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
        from .simulator import SimulatorConfig

        configuration_keys, configuration_unknown_keys = self._configuration_payload()

        return SimulatorConfig(
            host=self.host,
            ws_port=self.ws_port,
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
        if self.ws_port:
            return f"ws://{self.host}:{self.ws_port}/{path}"
        return f"ws://{self.host}/{path}"


class DataTransferMessage(Entity):
    """Persisted record of OCPP DataTransfer exchanges."""

    DIRECTION_CP_TO_CSMS = "cp_to_csms"
    DIRECTION_CSMS_TO_CP = "csms_to_cp"
    DIRECTION_CHOICES = (
        (DIRECTION_CP_TO_CSMS, _("Charge Point → CSMS")),
        (DIRECTION_CSMS_TO_CP, _("CSMS → Charge Point")),
    )

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="data_transfer_messages",
    )
    connector_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Connector ID",
    )
    direction = models.CharField(max_length=16, choices=DIRECTION_CHOICES)
    ocpp_message_id = models.CharField(
        max_length=64,
        verbose_name="OCPP message ID",
    )
    vendor_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Vendor ID",
    )
    message_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Message ID",
    )
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=64, blank=True)
    response_data = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_description = models.TextField(blank=True)
    error_details = models.JSONField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Data Message")
        verbose_name_plural = _("Data Messages")
        indexes = [
            models.Index(
                fields=["ocpp_message_id"],
                name="ocpp_datatr_ocpp_me_70d17f_idx",
            ),
            models.Index(
                fields=["vendor_id"], name="ocpp_datatr_vendor__59e1c7_idx"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.get_direction_display()} {self.vendor_id or 'DataTransfer'}"


class CPFirmwareRequest(Entity):
    """Temporary record tracking CP firmware requests."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="firmware_requests",
    )
    connector_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Connector ID",
    )
    vendor_id = models.CharField(max_length=255, blank=True)
    message = models.OneToOneField(
        DataTransferMessage,
        on_delete=models.CASCADE,
        related_name="firmware_request",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=64, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("CP Firmware Request")
        verbose_name_plural = _("CP Firmware Requests")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Firmware request for {self.charger}" if self.pk else "Firmware request"


class CPFirmware(Entity):
    """Persisted firmware packages associated with charge points."""

    class Source(models.TextChoices):
        DOWNLOAD = "download", _("Downloaded")
        UPLOAD = "upload", _("Uploaded")

    name = models.CharField(_("Name"), max_length=200, blank=True)
    description = models.TextField(_("Description"), blank=True)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.DOWNLOAD,
        verbose_name=_("Source"),
    )
    source_node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downloaded_firmware",
        verbose_name=_("Source node"),
    )
    source_charger = models.ForeignKey(
        "Charger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downloaded_firmware",
        verbose_name=_("Source charge point"),
    )
    content_type = models.CharField(
        _("Content type"),
        max_length=100,
        default="application/octet-stream",
        blank=True,
    )
    filename = models.CharField(_("Filename"), max_length=255, blank=True)
    payload_json = models.JSONField(null=True, blank=True)
    payload_binary = models.BinaryField(null=True, blank=True)
    payload_encoding = models.CharField(_("Encoding"), max_length=32, blank=True)
    payload_size = models.PositiveIntegerField(_("Payload size"), default=0)
    checksum = models.CharField(_("Checksum"), max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    download_vendor_id = models.CharField(
        _("Vendor ID"), max_length=255, blank=True
    )
    download_message_id = models.CharField(
        _("Message ID"), max_length=64, blank=True
    )
    downloaded_at = models.DateTimeField(_("Downloaded at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("CP Firmware")
        verbose_name_plural = _("CP Firmware")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.name or self.filename or ""
        if label:
            return label
        return f"Firmware #{self.pk}" if self.pk else "Firmware"

    def save(self, *args, **kwargs):
        if self.filename:
            self.filename = os.path.basename(self.filename)
        payload_bytes = self.get_payload_bytes()
        self.payload_size = len(payload_bytes)
        if payload_bytes:
            self.checksum = hashlib.sha256(payload_bytes).hexdigest()
        elif not self.checksum:
            self.checksum = ""
        if not self.content_type:
            if self.payload_binary:
                self.content_type = "application/octet-stream"
            elif self.payload_json is not None:
                self.content_type = "application/json"
        if not self.payload_encoding:
            self.payload_encoding = ""
        super().save(*args, **kwargs)

    def get_payload_bytes(self) -> bytes:
        if self.payload_binary:
            return bytes(self.payload_binary)
        if self.payload_json is not None:
            try:
                return json.dumps(
                    self.payload_json,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except (TypeError, ValueError):
                return str(self.payload_json).encode("utf-8")
        return b""

    @property
    def has_binary(self) -> bool:
        return bool(self.payload_binary)

    @property
    def has_json(self) -> bool:
        return self.payload_json is not None


class CPFirmwareDeployment(Entity):
    """Track firmware rollout attempts for specific charge points."""

    TERMINAL_STATUSES = {"Installed", "InstallationFailed", "DownloadFailed"}

    firmware = models.ForeignKey(
        CPFirmware,
        on_delete=models.CASCADE,
        related_name="deployments",
        verbose_name=_("Firmware"),
    )
    charger = models.ForeignKey(
        "Charger",
        on_delete=models.PROTECT,
        related_name="firmware_deployments",
        verbose_name=_("Charge point"),
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firmware_deployments",
        verbose_name=_("Node"),
    )
    ocpp_message_id = models.CharField(
        _("OCPP message ID"), max_length=64, blank=True
    )
    status = models.CharField(_("Status"), max_length=32, blank=True)
    status_info = models.CharField(_("Status details"), max_length=255, blank=True)
    status_timestamp = models.DateTimeField(_("Status timestamp"), null=True, blank=True)
    requested_at = models.DateTimeField(_("Requested at"), auto_now_add=True)
    completed_at = models.DateTimeField(_("Completed at"), null=True, blank=True)
    retrieve_date = models.DateTimeField(_("Retrieve date"), null=True, blank=True)
    retry_count = models.PositiveIntegerField(_("Retries"), default=0)
    retry_interval = models.PositiveIntegerField(
        _("Retry interval (seconds)"), default=0
    )
    download_token = models.CharField(_("Download token"), max_length=64, blank=True)
    download_token_expires_at = models.DateTimeField(
        _("Token expires at"), null=True, blank=True
    )
    downloaded_at = models.DateTimeField(_("Downloaded at"), null=True, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("CP Firmware Deployment")
        verbose_name_plural = _("CP Firmware Deployments")
        indexes = [
            models.Index(fields=["ocpp_message_id"]),
            models.Index(fields=["download_token"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.firmware} → {self.charger}" if self.pk else "Firmware Deployment"

    def issue_download_token(self, *, lifetime: timedelta | None = None) -> str:
        if lifetime is None:
            lifetime = timedelta(hours=1)
        deadline = timezone.now() + lifetime
        token = secrets.token_urlsafe(24)
        while type(self).all_objects.filter(download_token=token).exists():
            token = secrets.token_urlsafe(24)
        self.download_token = token
        self.download_token_expires_at = deadline
        self.save(
            update_fields=["download_token", "download_token_expires_at", "updated_at"]
        )
        return token

    def mark_status(
        self,
        status: str,
        info: str = "",
        timestamp: datetime | None = None,
        *,
        response: dict | None = None,
    ) -> None:
        timestamp_value = timestamp or timezone.now()
        self.status = status
        self.status_info = info
        self.status_timestamp = timestamp_value
        if response is not None:
            self.response_payload = response
        if status in self.TERMINAL_STATUSES and not self.completed_at:
            self.completed_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "status_info",
                "status_timestamp",
                "response_payload",
                "completed_at",
                "updated_at",
            ]
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES and bool(self.completed_at)


class CPReservation(Entity):
    """Track connector reservations dispatched to an EVCS."""

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name=_("Location"),
    )
    connector = models.ForeignKey(
        Charger,
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


class StationModelManager(EntityManager):
    def get_by_natural_key(self, vendor: str, model_family: str, model: str):
        return self.get(vendor=vendor, model_family=model_family, model=model)


class StationModel(Entity):
    """Supported EVCS hardware model."""

    vendor = models.CharField(_("Vendor"), max_length=100)
    model_family = models.CharField(_("Model Family"), max_length=100)
    model = models.CharField(_("Model"), max_length=100)
    max_power_kw = models.DecimalField(
        _("Max Power (kW)"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Maximum sustained charging power supported by this model."),
    )
    max_voltage_v = models.PositiveIntegerField(
        _("Max Voltage (V)"),
        null=True,
        blank=True,
        help_text=_("Maximum supported operating voltage."),
    )
    preferred_ocpp_version = models.CharField(
        _("Preferred OCPP Version"),
        max_length=16,
        blank=True,
        default="",
        help_text=_(
            "Optional OCPP protocol version usually paired with this EVCS model."
        ),
    )
    connector_type = models.CharField(
        _("Connector Type"),
        max_length=64,
        blank=True,
        help_text=_("Primary connector format supported by this model."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text=_("Optional comments about capabilities or certifications."),
    )

    objects = StationModelManager()

    class Meta:
        unique_together = ("vendor", "model_family", "model")
        verbose_name = _("Station Model")
        verbose_name_plural = _("Station Models")
        db_table = "core_stationmodel"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        parts = [self.vendor]
        if self.model_family:
            parts.append(self.model_family)
        if self.model:
            parts.append(self.model)
        return " ".join(part for part in parts if part)

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.vendor, self.model_family, self.model)


def sync_forwarded_charge_points(*, refresh_forwarders: bool = True) -> int:
    """Proxy to the OCPP forwarder for testability."""

    from .forwarder import forwarder

    return forwarder.sync_forwarded_charge_points(
        refresh_forwarders=refresh_forwarders
    )


def is_target_active(target_id: int | None) -> bool:
    """Proxy to check whether a forwarding session is active."""

    from .forwarder import forwarder

    return forwarder.is_target_active(target_id)


OCPP_FORWARDING_MESSAGES: Sequence[str] = (
    "Authorize",
    "BootNotification",
    "DataTransfer",
    "DiagnosticsStatusNotification",
    "FirmwareStatusNotification",
    "Heartbeat",
    "MeterValues",
    "StartTransaction",
    "StatusNotification",
    "StopTransaction",
    "ClearedChargingLimit",
    "CostUpdated",
    "Get15118EVCertificate",
    "GetCertificateStatus",
    "LogStatusNotification",
    "NotifyChargingLimit",
    "NotifyCustomerInformation",
    "NotifyDisplayMessages",
    "NotifyEVChargingNeeds",
    "NotifyEVChargingSchedule",
    "NotifyEvent",
    "NotifyMonitoringReport",
    "NotifyReport",
    "PublishFirmwareStatusNotification",
    "ReportChargingProfiles",
    "ReservationStatusUpdate",
    "SecurityEventNotification",
    "SignCertificate",
    "TransactionEvent",
)


def default_forwarded_messages() -> list[str]:
    return list(OCPP_FORWARDING_MESSAGES)


class CPForwarderManager(EntityManager):
    """Manager adding helpers for charge point forwarders."""

    def sync_forwarding_targets(self) -> None:
        for forwarder in self.all():
            forwarder.sync_chargers(apply_sessions=False)

    def update_running_state(self, active_target_ids: Iterable[int]) -> None:
        active = set(active_target_ids)
        for forwarder in self.all():
            forwarder.set_running_state(forwarder.target_node_id in active)


class CPForwarder(Entity):
    """Configuration for forwarding local charge point traffic to a remote node."""

    name = models.CharField(
        _("Name"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("Optional label used when listing this forwarder."),
    )
    source_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        related_name="outgoing_forwarders",
        null=True,
        blank=True,
        help_text=_("Node providing the original charge point connection."),
    )
    target_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="incoming_forwarders",
        help_text=_("Remote node that will receive forwarded sessions."),
    )
    enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Enable to forward eligible charge points to the remote node. "
            "Charge points must also have Export transactions enabled."
        ),
    )
    forwarded_messages = models.JSONField(
        default=default_forwarded_messages,
        blank=True,
        help_text=_(
            "Select the OCPP messages that should be forwarded to the remote node."
        ),
    )
    is_running = models.BooleanField(
        default=False,
        editable=False,
        help_text=_("Indicates whether an active forwarding websocket is connected."),
    )
    last_forwarded_at = models.DateTimeField(
        _("Last forwarded at"),
        null=True,
        blank=True,
        help_text=_("Timestamp of the most recent forwarding activity."),
    )
    last_status = models.CharField(
        _("Last status"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Summary of the last forwarding synchronization."),
    )
    last_error = models.CharField(
        _("Last error"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Most recent error encountered while configuring forwarding."),
    )
    last_synced_at = models.DateTimeField(
        _("Last synced at"),
        null=True,
        blank=True,
        help_text=_("When the forwarder last attempted to apply its configuration."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CPForwarderManager()

    class Meta:
        verbose_name = _("CP Forwarder")
        verbose_name_plural = _("CP Forwarders")
        constraints = [
            models.UniqueConstraint(
                fields=["source_node", "target_node"], name="unique_forwarder_per_target"
            )
        ]
        ordering = ["target_node__hostname", "target_node__pk"]
        db_table = "protocols_cpforwarder"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.name:
            return self.name
        label = str(self.target_node) if self.target_node else _("Unconfigured")
        if self.source_node:
            return f"{self.source_node} → {label}"
        return label

    def save(self, *args, **kwargs):
        sync_chargers = kwargs.pop("sync_chargers", True)
        self.forwarded_messages = self.sanitize_forwarded_messages(
            self.forwarded_messages
        )
        if self.source_node_id is None:
            local = Node.get_local()
            if local:
                self.source_node = local
        super().save(*args, **kwargs)
        if sync_chargers:
            self.sync_chargers()

    def delete(self, *args, **kwargs):
        was_enabled = self.enabled
        if was_enabled:
            self.enabled = False
        self.sync_chargers()
        super().delete(*args, **kwargs)
        sync_forwarded_charge_points()

    def sync_chargers(self, *, apply_sessions: bool = True) -> None:
        """Apply the forwarder configuration to eligible charge points."""

        from .forwarding_utils import (
            attempt_forwarding_probe,
            load_local_node_credentials,
            send_forwarding_metadata,
        )

        now = timezone.now()
        local_node, private_key, credential_error = load_local_node_credentials()
        eligible = self._eligible_chargers(local_node)
        status_parts: list[str] = []
        error_message: str | None = None
        session_active: bool | None = None

        if not self.target_node_id:
            error_message = _("Target node is not configured.")
            status_parts.append(_("Forwarder configuration is incomplete."))
            status_text = " ".join(str(part) for part in status_parts if str(part).strip()).strip()
            error_text = str(error_message) if error_message else ""
            self._update_fields(
                last_status=status_text,
                last_error=error_text,
                last_synced_at=now,
                is_running=False,
            )
            if apply_sessions:
                sync_forwarded_charge_points(refresh_forwarders=False)
            return

        if not self.enabled:
            cleared = eligible.filter(forwarded_to=self.target_node).update(
                forwarded_to=None, forwarding_watermark=None
            )
            if cleared:
                status_parts.append(
                    _("Cleared forwarding for %(count)s charge point(s).")
                    % {"count": cleared}
                )
            else:
                status_parts.append(_("No forwarded charge points required clearing."))
            status_text = " ".join(str(part) for part in status_parts if str(part).strip()).strip()
            updates = {
                "last_status": status_text,
                "last_error": "",
                "last_synced_at": now,
            }
            if self.is_running:
                updates["is_running"] = False
            self._update_fields(**updates)
            if apply_sessions:
                sync_forwarded_charge_points(refresh_forwarders=False)
            return

        conflicts = eligible.exclude(
            Q(forwarded_to__isnull=True) | Q(forwarded_to=self.target_node)
        ).count()
        chargers_to_update = list(
            eligible.filter(
                Q(forwarded_to__isnull=True) | Q(forwarded_to=self.target_node)
            )
        )

        updated_count = 0
        if chargers_to_update:
            from apps.ocpp.models import Charger

            charger_pks = [charger.pk for charger in chargers_to_update if charger.pk]
            if charger_pks:
                Charger.objects.filter(pk__in=charger_pks).update(
                    forwarded_to=self.target_node
                )
                updated_count = len(charger_pks)
                status_parts.append(
                    _("Forwarding %(count)s charge point(s).")
                    % {"count": updated_count}
                )
        else:
            status_parts.append(_("No charge points were updated."))

        if conflicts:
            status_parts.append(
                _("Skipped %(count)s charge point(s) already forwarded elsewhere.")
                % {"count": conflicts}
            )

        sample = next((charger for charger in chargers_to_update if charger.charger_id), None)
        if sample and not attempt_forwarding_probe(self.target_node, sample.charger_id):
            status_parts.append(
                _("Probe failed for %(charger)s.") % {"charger": sample.charger_id}
            )

        if updated_count:
            if credential_error:
                error_message = credential_error
            elif local_node is None or private_key is None:
                error_message = _(
                    "Unable to sign forwarding metadata without a configured private key."
                )
            else:
                success, metadata_error = send_forwarding_metadata(
                    self.target_node,
                    chargers_to_update,
                    local_node,
                    private_key,
                    forwarded_messages=self.get_forwarded_messages(),
                )
                if success:
                    status_parts.append(
                        _("Forwarding metadata sent to %(node)s.")
                        % {"node": self.target_node}
                    )
                else:
                    error_message = metadata_error or _(
                        "Failed to send forwarding metadata to the remote node."
                    )

        elif credential_error and not error_message:
            error_message = credential_error

        if apply_sessions:
            sync_forwarded_charge_points(refresh_forwarders=False)
            session_active = is_target_active(self.target_node_id)
            targeted_exists = eligible.filter(forwarded_to=self.target_node).exists()
            if session_active:
                status_parts.append(_("Forwarding websocket is connected."))
            elif targeted_exists:
                status_parts.append(_("Forwarding websocket is not connected."))
                if not error_message:
                    error_message = _("Forwarding websocket inactive.")

        status_text = " ".join(str(part) for part in status_parts if str(part).strip()).strip()
        error_text = str(error_message) if error_message else ""
        updates = {
            "last_status": status_text,
            "last_error": error_text,
            "last_synced_at": now,
        }
        if apply_sessions:
            desired_running = bool(self.enabled and session_active)
            if self.is_running != desired_running:
                updates["is_running"] = desired_running
        self._update_fields(**updates)

    def mark_running(self, timestamp) -> None:
        updates = {}
        if not self.is_running:
            updates["is_running"] = True
        if timestamp and (self.last_forwarded_at is None or timestamp > self.last_forwarded_at):
            updates["last_forwarded_at"] = timestamp
        if updates:
            self._update_fields(**updates)

    def set_running_state(self, active: bool) -> None:
        if active and not self.is_running:
            self._update_fields(is_running=True)
        elif not active and self.is_running:
            self._update_fields(is_running=False)

    def _eligible_chargers(self, local_node: Node | None):
        from apps.ocpp.models import Charger

        qs = Charger.objects.filter(export_transactions=True)
        if local_node and local_node.pk:
            qs = qs.filter(Q(node_origin=local_node) | Q(node_origin__isnull=True))
        return qs.select_related("forwarded_to")

    def _update_fields(self, **changes) -> None:
        if not changes or not self.pk:
            for key, value in changes.items():
                setattr(self, key, value)
            return
        type(self).objects.filter(pk=self.pk).update(**changes)
        for key, value in changes.items():
            setattr(self, key, value)

    @classmethod
    def available_forwarded_messages(cls) -> Sequence[str]:
        return tuple(OCPP_FORWARDING_MESSAGES)

    @classmethod
    def sanitize_forwarded_messages(cls, values: Iterable[str] | None) -> list[str]:
        if values is None:
            return list(OCPP_FORWARDING_MESSAGES)
        if isinstance(values, str):
            return list(OCPP_FORWARDING_MESSAGES)
        cleaned: list[str] = []
        order_map = {msg: idx for idx, msg in enumerate(OCPP_FORWARDING_MESSAGES)}
        for item in values:
            if item in order_map and item not in cleaned:
                cleaned.append(item)
        if cleaned:
            cleaned.sort(key=lambda msg: order_map[msg])
            return cleaned
        if isinstance(values, list) and not values:
            return []
        return list(OCPP_FORWARDING_MESSAGES)

    def get_forwarded_messages(self) -> list[str]:
        return self.sanitize_forwarded_messages(self.forwarded_messages)

    def forwards_action(self, action: str) -> bool:
        if not action:
            return False
        return action in self.get_forwarded_messages()

    @property
    def _forwarding_service(self):
        """Return the shared forwarding service used to manage sessions."""

        from apps.ocpp.forwarder import forwarder

        return forwarder

    @property
    def _sessions(self):
        """Expose active sessions tracked by the forwarding service."""

        return self._forwarding_service._sessions

    def get_session(self, charger_pk: int):
        """Return the active forwarding session for ``charger_pk`` when present."""

        return self._forwarding_service.get_session(charger_pk)

    def remove_session(self, charger_pk: int) -> None:
        """Remove the forwarding session for ``charger_pk`` if it exists."""

        self._forwarding_service.remove_session(charger_pk)


class EVCSChargePointManager(EntityManager):
    def get_queryset(self):
        return super().get_queryset().filter(connector_id__isnull=True)


class EVCSChargePoint(Charger):
    objects = EVCSChargePointManager()

    class Meta:
        proxy = True
        verbose_name = _("EVCS Charge Point")
        verbose_name_plural = _("EVCS Charge Points")


class RFID(CoreRFID):
    class Meta:
        proxy = True
        app_label = "ocpp"
        verbose_name = CoreRFID._meta.verbose_name
        verbose_name_plural = CoreRFID._meta.verbose_name_plural
