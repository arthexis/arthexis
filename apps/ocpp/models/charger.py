from __future__ import annotations

from django.urls import NoReverseMatch

from apps.locale.models import Language

from .. import store
from .base import *
from .transaction import annotate_transaction_energy_bounds

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
    language = models.ForeignKey(
        Language,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chargers",
        verbose_name=_("Language"),
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
    configuration_check_enabled = models.BooleanField(
        _("Configuration Check"),
        default=False,
        help_text=_("Allow scheduled configuration checks for this charger."),
    )
    power_projection_enabled = models.BooleanField(
        _("Power Projection"),
        default=False,
        help_text=_("Allow scheduled power projection requests for this charger."),
    )
    firmware_snapshot_enabled = models.BooleanField(
        _("Firmware Snapshot"),
        default=False,
        help_text=_("Allow scheduled firmware snapshot requests for this charger."),
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
    last_charging_limit = models.JSONField(default=dict, blank=True)
    last_charging_limit_source = models.CharField(max_length=120, blank=True, default="")
    last_charging_limit_is_grid_critical = models.BooleanField(null=True, blank=True)
    last_charging_limit_at = models.DateTimeField(null=True, blank=True)
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
        default=True,
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

    def language_code(self) -> str:
        """Return the configured language code for this charger, if any."""

        language = getattr(self, "language", None)
        return (language.code or "").strip() if language else ""

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

    @property
    def last_seen(self):
        """Return the most recent activity timestamp for the charger."""

        return self.last_status_timestamp or self.last_heartbeat

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
            try:
                return reverse("ocpp:charger-page", args=[serial])
            except NoReverseMatch:
                return reverse("charger-page", args=[serial])
        try:
            return reverse("ocpp:charger-page-connector", args=[serial, connector_slug])
        except NoReverseMatch:
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
            if (
                local_node
                and local_node.pk
                and Node.objects.filter(pk=local_node.pk).exists()
            ):
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
        from .. import store

        total = 0.0
        for charger in self._target_chargers():
            total += charger._total_kw_single(store)
        return total

    def _store_keys(self) -> list[str]:
        """Return keys used for store lookups with fallbacks."""

        from .. import store

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
        for charger in self._target_chargers():
            charger.transactions.all().delete()
            charger.meter_values.all().delete()
            for key in charger._store_keys():
                store.clear_log(key, log_type="charger")
                store.transactions.pop(key, None)
                store.history.pop(key, None)

    def delete(self, *args, **kwargs):
        from django.db.models.deletion import ProtectedError

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
