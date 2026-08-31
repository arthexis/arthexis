from __future__ import annotations

import base64
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlsplit

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.cards.classic_layout import normalize_card_name
from apps.cards.command_layout import (
    COMMAND_LIFECYCLE_TRIGGERED,
    CardLayoutError,
    DecodedCommandCard,
    command_payload_digest,
    lifecycle_mode_from_flags,
    normalize_command_lifecycle_mode,
)

__all__ = ["RFIDCommandTemplate"]


def _load_qrcode_module():
    try:
        import qrcode
    except ModuleNotFoundError as exc:
        if exc.name == "qrcode":
            return None
        raise
    return qrcode


class RFIDCommandTemplate(Entity):
    """Reusable suite command-card payload that can be burned to RFID cards."""

    class Source(models.TextChoices):
        BUNDLED = "bundled", _("Bundled")
        CUSTOM = "custom", _("Custom")
        DISCOVERED = "discovered", _("Discovered")

    class ViewKind(models.TextChoices):
        GENERAL = "general", _("General")
        COMMAND_OUTPUT = "command_output", _("Command output")
        FEEDBACK = "feedback", _("Feedback reporting")
        HEALTH = "health", _("Health")
        UPGRADE = "upgrade", _("Upgrade")

    name = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        help_text=_("Natural key stored in sector 0 block 1 on command cards."),
    )
    slug = models.SlugField(max_length=64, unique=True)
    title = models.CharField(max_length=128, blank=True)
    description = models.TextField(blank=True)
    instructions = models.TextField(
        blank=True,
        help_text=_("Operator-facing instructions shown on the public card view."),
    )
    command_name = models.CharField(max_length=64, db_index=True)
    command_params = models.JSONField(default=dict, blank=True)
    command_sigils = models.JSONField(default=dict, blank=True)
    lifecycle_mode = models.CharField(
        max_length=16,
        choices=(
            ("triggered", _("Triggered")),
            ("reader_held", _("Reader held")),
        ),
        default="triggered",
        help_text=_(
            "Whether cards burned from this template trigger once or only run "
            "while held on the reader."
        ),
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.CUSTOM,
        db_index=True,
    )
    view_kind = models.CharField(
        max_length=32,
        choices=ViewKind.choices,
        default=ViewKind.GENERAL,
    )
    qr_target_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Optional URL or path encoded by this template's QR code. "
            "Defaults to the public command-template detail page."
        ),
    )
    is_active = models.BooleanField(default=True)
    requires_owner = models.BooleanField(
        default=True,
        help_text=_(
            "Cards copied from this template should be owned before execution."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source", "name"]
        verbose_name = _("RFID command template")
        verbose_name_plural = _("RFID command templates")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display_title

    @property
    def display_title(self) -> str:
        return self.title or self.name

    @property
    def payload_digest(self) -> str:
        return command_payload_digest(
            name=self.name,
            command=self.command_name,
            params=self.command_params,
            sigils=self.command_sigils,
        )

    def _validate_command_json_objects(self) -> None:
        errors = {}
        if not isinstance(self.command_params, dict):
            errors["command_params"] = _("Command parameters must be a JSON object.")
        if not isinstance(self.command_sigils, dict):
            errors["command_sigils"] = _("Command sigils must be a JSON object.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.name = normalize_card_name(self.name)
        if self.command_name:
            self.command_name = str(self.command_name).strip().upper()[:64]
        self._validate_command_json_objects()
        if not self.title:
            self.title = self.name.title()
        if not self.slug:
            self.slug = self._unique_slug(self.name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "name",
                "command_name",
                "command_params",
                "command_sigils",
                "title",
                "slug",
                "updated_at",
            }
        super().save(*args, **kwargs)

    @classmethod
    def _unique_slug(cls, value: str) -> str:
        base = slugify(value)[:48] or "command-card"
        candidate = base
        counter = 2
        while cls.all_objects.filter(slug=candidate).exists():
            suffix = f"-{counter}"
            candidate = f"{base[:64 - len(suffix)]}{suffix}"
            counter += 1
        return candidate

    @classmethod
    def for_card(cls, card: DecodedCommandCard) -> RFIDCommandTemplate | None:
        if not card.name:
            return None
        return cls.objects.filter(name=normalize_card_name(card.name)).first()

    @classmethod
    def discover_from_card(
        cls, card: DecodedCommandCard
    ) -> tuple[RFIDCommandTemplate | None, bool]:
        if not card.command:
            return None, False
        name = normalize_card_name(card.name or f"DISC {card.command}"[:16])
        existing = cls.objects.filter(name=name).first()
        if existing is not None:
            return existing, False
        try:
            return (
                cls.objects.create(
                    name=name,
                    title=name.title(),
                    description=_("Discovered from a command card seen by this node."),
                    command_name=card.command,
                    command_params=card.params,
                    command_sigils=card.sigils,
                    lifecycle_mode=card.metadata.lifecycle_mode,
                    source=cls.Source.DISCOVERED,
                    view_kind=cls.ViewKind.GENERAL,
                    is_active=False,
                ),
                True,
            )
        except IntegrityError:
            return cls.objects.filter(name=name).first(), False

    def get_absolute_url(self) -> str:
        return reverse("rfid-command-template-detail", kwargs={"slug": self.slug})

    def get_qr_target_path(self) -> str:
        """Return the URL/path that QR codes for this template should encode."""

        return (self.qr_target_path or "").strip() or self.get_absolute_url()

    def get_qr_target_url(self, base_url: str = "") -> str:
        """Return an absolute QR target when ``base_url`` is provided."""

        target = self.get_qr_target_path()
        if urlsplit(target).scheme or target.startswith("//"):
            return target
        if not base_url:
            return target
        return urljoin(base_url.rstrip("/") + "/", target.lstrip("/"))

    def qr_data_uri(self, url: str) -> str:
        qrcode_module = _load_qrcode_module()
        if qrcode_module is None:
            return ""
        qr = qrcode_module.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
            "ascii"
        )

    def card_consistency(self, rfid, latest_execution=None) -> dict[str, Any]:
        expected_digest = self.payload_digest
        card_digest = str(getattr(rfid, "command_payload_digest", "") or "")
        name_matches = getattr(rfid, "command_card_name", "") == self.name
        payload_matches = card_digest.lower() == expected_digest.lower()
        card_lifecycle_mode = self._card_lifecycle_mode(rfid)
        lifecycle_matches = card_lifecycle_mode == self.lifecycle_mode
        linked = getattr(rfid, "command_template_id", None) == self.pk
        if latest_execution is None:
            latest_execution = (
                self.command_executions.filter(rfid=rfid)
                .order_by("-triggered_at")
                .first()
            )
        result_matches = True
        if latest_execution is not None and latest_execution.result_digest:
            result_matches = (
                str(getattr(rfid, "command_result_digest", "") or "").lower()
                == str(latest_execution.result_digest or "").lower()
            )
        return {
            "rfid": rfid,
            "linked": linked,
            "name_matches": name_matches,
            "payload_matches": payload_matches,
            "lifecycle_matches": lifecycle_matches,
            "card_lifecycle_mode": card_lifecycle_mode,
            "result_matches": result_matches,
            "valid": (
                linked
                and name_matches
                and payload_matches
                and lifecycle_matches
                and result_matches
            ),
            "latest_execution": latest_execution,
        }

    def _card_lifecycle_mode(self, rfid) -> str:
        metadata = getattr(rfid, "command_card_metadata", None)
        if isinstance(metadata, dict):
            lifecycle_mode = metadata.get("lifecycle_mode")
            if lifecycle_mode not in (None, ""):
                try:
                    return normalize_command_lifecycle_mode(lifecycle_mode)
                except CardLayoutError:
                    return ""
            flags = metadata.get("flags")
            if flags not in (None, ""):
                try:
                    return lifecycle_mode_from_flags(int(flags))
                except (TypeError, ValueError):
                    return ""
        return COMMAND_LIFECYCLE_TRIGGERED
