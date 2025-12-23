from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

import qrcode
from django.apps import apps
from django.core.management.color import no_style
from django.core.validators import RegexValidator
from django.db import IntegrityError, connections, models, transaction
from django.db.models import Q
from django.db.models.functions import Length
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


__all__ = ["RFID"]


class RFID(Entity):
    """RFID tag that may be assigned to one account."""

    label_id = models.AutoField(primary_key=True, db_column="label_id")
    MATCH_PREFIX_LENGTH = 8
    rfid = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="RFID",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]+$",
                message="RFID must be hexadecimal digits",
            )
        ],
    )
    reversed_uid = models.CharField(
        max_length=255,
        default="",
        blank=True,
        editable=False,
        verbose_name="Reversed UID",
        help_text="UID value stored with opposite endianness for reference.",
    )
    custom_label = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="Custom Label",
        help_text="Optional custom label for this RFID.",
    )
    key_a = models.CharField(
        max_length=12,
        default="FFFFFFFFFFFF",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{12}$",
                message="Key must be 12 hexadecimal digits",
            )
        ],
        verbose_name="Key A",
    )
    key_b = models.CharField(
        max_length=12,
        default="FFFFFFFFFFFF",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{12}$",
                message="Key must be 12 hexadecimal digits",
            )
        ],
        verbose_name="Key B",
    )
    data = models.JSONField(
        default=list,
        blank=True,
        help_text="Sector and block data",
    )
    key_a_verified = models.BooleanField(default=False)
    key_b_verified = models.BooleanField(default=False)
    allowed = models.BooleanField(default=True)
    external_command = models.TextField(
        default="",
        blank=True,
        help_text="Optional command executed during validation.",
    )
    post_auth_command = models.TextField(
        default="",
        blank=True,
        help_text="Optional command executed after successful validation.",
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional expiration date for this RFID card.",
    )
    BLACK = "B"
    WHITE = "W"
    BLUE = "U"
    RED = "R"
    GREEN = "G"
    COLOR_CHOICES = [
        (BLACK, _("Black")),
        (WHITE, _("White")),
        (BLUE, _("Blue")),
        (RED, _("Red")),
        (GREEN, _("Green")),
    ]
    SCAN_LABEL_STEP = 10
    COPY_LABEL_STEP = 1
    color = models.CharField(
        max_length=1,
        choices=COLOR_CHOICES,
        default=BLACK,
    )
    CLASSIC = "CLASSIC"
    NTAG215 = "NTAG215"
    KIND_CHOICES = [
        (CLASSIC, _("MIFARE Classic")),
        (NTAG215, _("NTAG215")),
    ]
    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        default=CLASSIC,
        verbose_name="Kind",
    )
    BIG_ENDIAN = "BIG"
    LITTLE_ENDIAN = "LITTLE"
    ENDIANNESS_CHOICES = [
        (BIG_ENDIAN, _("Big endian")),
        (LITTLE_ENDIAN, _("Little endian")),
    ]
    endianness = models.CharField(
        max_length=6,
        choices=ENDIANNESS_CHOICES,
        default=BIG_ENDIAN,
    )
    reference = models.ForeignKey(
        "links.Reference",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rfids",
        help_text="Optional reference for this RFID.",
    )
    origin_node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_rfids",
        help_text="Node where this RFID record was created.",
    )
    released = models.BooleanField(default=False)
    added_on = models.DateTimeField(auto_now_add=True)
    last_seen_on = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if not self.origin_node_id:
            try:
                from apps.nodes.models import Node  # imported lazily to avoid circular import
            except Exception:  # pragma: no cover - nodes app may be unavailable
                node = None
            else:
                node = Node.get_local()
            if node:
                self.origin_node = node
                if update_fields:
                    fields = set(update_fields)
                    if "origin_node" not in fields:
                        fields.add("origin_node")
                        kwargs["update_fields"] = tuple(fields)
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values("key_a", "key_b").first()
            if old:
                if self.key_a and old["key_a"] != self.key_a.upper():
                    self.key_a_verified = False
                if self.key_b and old["key_b"] != self.key_b.upper():
                    self.key_b_verified = False
        if self.rfid:
            normalized_rfid = self.rfid.upper()
            self.rfid = normalized_rfid
            reversed_uid = self.reverse_uid(normalized_rfid)
            if reversed_uid != self.reversed_uid:
                self.reversed_uid = reversed_uid
                if update_fields:
                    fields = set(update_fields)
                    if "reversed_uid" not in fields:
                        fields.add("reversed_uid")
                        kwargs["update_fields"] = tuple(fields)
        if self.key_a:
            self.key_a = self.key_a.upper()
        if self.key_b:
            self.key_b = self.key_b.upper()
        if self.kind:
            self.kind = self.kind.upper()
        if self.endianness:
            self.endianness = self.normalize_endianness(self.endianness)
        super().save(*args, **kwargs)
        if not self.allowed:
            self.energy_accounts.clear()

    def __str__(self):  # pragma: no cover - simple representation
        return str(self.label_id)

    def qr_test_link(self) -> str:
        """Return a link that previews this RFID value as a QR code."""

        if not self.rfid:
            return ""
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(self.rfid)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
            "ascii"
        )
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            data_uri,
            _("Open QR preview"),
        )

    qr_test_link.short_description = _("QR test link")

    @classmethod
    def normalize_code(cls, value: str) -> str:
        """Return ``value`` normalized for comparisons."""

        return "".join((value or "").split()).upper()

    def adopt_rfid(self, candidate: str) -> bool:
        """Adopt ``candidate`` as the stored RFID if it is a better match."""

        normalized = type(self).normalize_code(candidate)
        if not normalized:
            return False
        current = type(self).normalize_code(self.rfid)
        if current == normalized:
            return False
        if not current:
            self.rfid = normalized
            return True
        reversed_current = type(self).reverse_uid(current)
        if reversed_current and reversed_current == normalized:
            self.rfid = normalized
            return True
        if len(normalized) < len(current):
            self.rfid = normalized
            return True
        if len(normalized) == len(current) and normalized < current:
            self.rfid = normalized
            return True
        return False

    @classmethod
    def matching_queryset(cls, value: str) -> models.QuerySet["RFID"]:
        """Return RFID records matching ``value`` using prefix comparison."""

        normalized = cls.normalize_code(value)
        if not normalized:
            return cls.objects.none()

        conditions: list[Q] = []
        candidate = normalized
        if candidate:
            conditions.append(Q(rfid=candidate))
        alternate = cls.reverse_uid(candidate)
        if alternate and alternate != candidate:
            conditions.append(Q(rfid=alternate))

        prefix_length = min(len(candidate), cls.MATCH_PREFIX_LENGTH)
        if prefix_length:
            prefix = candidate[:prefix_length]
            conditions.append(Q(rfid__startswith=prefix))
            if alternate and alternate != candidate:
                alt_prefix = alternate[:prefix_length]
                if alt_prefix:
                    conditions.append(Q(rfid__startswith=alt_prefix))

        query: Q | None = None
        for condition in conditions:
            query = condition if query is None else query | condition

        if query is None:
            return cls.objects.none()

        queryset = cls.objects.filter(query).distinct()
        return queryset.annotate(rfid_length=Length("rfid")).order_by(
            "rfid_length", "rfid", "pk"
        )

    @classmethod
    def find_match(cls, value: str) -> "RFID | None":
        """Return the best matching RFID for ``value`` if it exists."""

        return cls.matching_queryset(value).first()

    @classmethod
    def update_or_create_from_code(
        cls, value: str, defaults: dict[str, Any] | None = None
    ) -> tuple["RFID", bool]:
        """Update or create an RFID using relaxed matching rules."""

        normalized = cls.normalize_code(value)
        if not normalized:
            raise ValueError("RFID value is required")

        defaults_map = defaults.copy() if defaults else {}
        existing = cls.find_match(normalized)
        if existing:
            update_fields: set[str] = set()
            if existing.adopt_rfid(normalized):
                update_fields.add("rfid")
            for field_name, new_value in defaults_map.items():
                if getattr(existing, field_name) != new_value:
                    setattr(existing, field_name, new_value)
                    update_fields.add(field_name)
            if update_fields:
                existing.save(update_fields=sorted(update_fields))
            return existing, False

        create_kwargs = defaults_map
        create_kwargs["rfid"] = normalized
        tag = cls.objects.create(**create_kwargs)
        return tag, True

    @classmethod
    def normalize_endianness(cls, value: object) -> str:
        """Return a valid endianness value, defaulting to BIG."""

        if isinstance(value, str):
            candidate = value.strip().upper()
            valid = {choice[0] for choice in cls.ENDIANNESS_CHOICES}
            if candidate in valid:
                return candidate
        return cls.BIG_ENDIAN

    @staticmethod
    def reverse_uid(value: str) -> str:
        """Return ``value`` with reversed byte order for reference storage."""

        normalized = "".join((value or "").split()).upper()
        if not normalized:
            return ""
        if len(normalized) % 2 != 0:
            return normalized[::-1]
        bytes_list = [normalized[index : index + 2] for index in range(0, len(normalized), 2)]
        bytes_list.reverse()
        return "".join(bytes_list)

    @classmethod
    def next_scan_label(
        cls, *, step: int | None = None, start: int | None = None
    ) -> int:
        """Return the next label id for RFID tags created by scanning."""

        step_value = step or cls.SCAN_LABEL_STEP
        if step_value <= 0:
            raise ValueError("step must be a positive integer")
        start_value = start if start is not None else step_value

        labels_qs = (
            cls.objects.order_by("-label_id").values_list("label_id", flat=True)
        )
        max_label = 0
        last_multiple = 0
        for value in labels_qs.iterator():
            if value is None:
                continue
            if max_label == 0:
                max_label = value
            if value >= start_value and value % step_value == 0:
                last_multiple = value
                break
        if last_multiple:
            candidate = last_multiple + step_value
        else:
            candidate = start_value
        if max_label:
            while candidate <= max_label:
                candidate += step_value
        return candidate

    @classmethod
    def next_copy_label(
        cls, source: "RFID", *, step: int | None = None
    ) -> int:
        """Return the next label id when copying ``source`` to a new card."""

        step_value = step or cls.COPY_LABEL_STEP
        if step_value <= 0:
            raise ValueError("step must be a positive integer")
        base_label = (source.label_id or 0) + step_value
        candidate = base_label if base_label > 0 else step_value
        while cls.objects.filter(label_id=candidate).exists():
            candidate += step_value
        return candidate

    @classmethod
    def _reset_label_sequence(cls) -> None:
        """Ensure the PK sequence is at or above the current max label id."""

        connection = connections[cls.objects.db]
        reset_sql = connection.ops.sequence_reset_sql(no_style(), [cls])
        if not reset_sql:
            return
        with connection.cursor() as cursor:
            for statement in reset_sql:
                cursor.execute(statement)

    @classmethod
    def register_scan(
        cls,
        rfid: str,
        *,
        kind: str | None = None,
        endianness: str | None = None,
    ) -> tuple["RFID", bool]:
        """Return or create an RFID that was detected via scanning."""

        normalized = cls.normalize_code(rfid)
        desired_endianness = cls.normalize_endianness(endianness)
        existing = cls.find_match(normalized)
        if existing:
            update_fields: list[str] = []
            if existing.adopt_rfid(normalized):
                update_fields.append("rfid")
            if existing.endianness != desired_endianness:
                existing.endianness = desired_endianness
                update_fields.append("endianness")
            if update_fields:
                existing.save(update_fields=update_fields)
            return existing, False

        attempts = 0
        max_attempts = 10
        while attempts < max_attempts:
            attempts += 1
            label_id = cls.next_scan_label()
            create_kwargs = {
                "label_id": label_id,
                "rfid": normalized,
                "allowed": True,
                "released": False,
                "endianness": desired_endianness,
            }
            if kind:
                create_kwargs["kind"] = kind
            try:
                with transaction.atomic():
                    tag = cls.objects.create(**create_kwargs)
                    cls._reset_label_sequence()
            except IntegrityError:
                existing = cls.find_match(normalized)
                if existing:
                    return existing, False
            else:
                return tag, True
        raise IntegrityError("Unable to allocate label id for scanned RFID")

    @classmethod
    def get_account_by_rfid(cls, value):
        """Return the customer account associated with an RFID code if it exists."""

        try:
            CustomerAccount = apps.get_model("energy", "CustomerAccount")
        except LookupError:  # pragma: no cover - energy app optional
            return None
        matches = cls.matching_queryset(value).filter(allowed=True)
        if not matches.exists():
            return None
        return (
            CustomerAccount.objects.filter(rfids__in=matches)
            .distinct()
            .first()
        )

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        db_table = "core_rfid"

