"""Models for NFT inventory and RFID bindings."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class NFT(Entity):
    """Represents a tracked NFT payload that can be mirrored to RFID cards."""

    token_id = models.CharField(
        max_length=128,
        unique=True,
        help_text=_("Unique token identifier from the NFT source."),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Optional human-friendly NFT name."),
    )
    payload = models.BinaryField(
        default=bytes,
        blank=True,
        help_text=_("Raw NFT payload bytes that can be written onto an RFID card."),
    )
    payload_mime_type = models.CharField(
        max_length=127,
        default="application/octet-stream",
        help_text=_("MIME type for the payload bytes."),
    )

    class Meta:
        ordering = ("token_id",)

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return self.token_id


class RFIDBoundIdentity(Entity):
    """Logical NFT identity that can move between physical RFID cards."""

    identity_key = models.CharField(
        max_length=128,
        unique=True,
        help_text=_("Stable identity key independent from the physical card RFID."),
    )
    nft = models.ForeignKey(
        "nfts.NFT",
        on_delete=models.CASCADE,
        related_name="bound_identities",
    )
    current_rfid = models.ForeignKey(
        "cards.RFID",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nft_identities",
        help_text=_("Current card carrying this identity."),
    )
    card_payload = models.BinaryField(
        default=bytes,
        blank=True,
        help_text=_("Bytes intended to be stored directly in the target RFID card."),
    )
    payload_written_to_card = models.BooleanField(
        default=False,
        help_text=_("Indicates whether card_payload was written to the physical card."),
    )
    last_transferred_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("identity_key",)

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return self.identity_key

    def clean(self) -> None:
        """Validate the model state before persistence."""

        super().clean()
        if self.current_rfid_id and not self.current_rfid.allowed:
            raise ValidationError(
                {"current_rfid": _("Cannot bind identity to a blocked RFID card.")}
            )

    def transfer_to_rfid(self, target_rfid: "models.Model", *, actor: str = "") -> "NFTTransfer":
        """Transfer this identity to ``target_rfid`` and persist transfer history."""

        if target_rfid is None:
            raise ValueError("target_rfid is required for transfer")
        if not target_rfid.allowed:
            raise ValidationError(_("Target RFID card is blocked and cannot receive identities."))

        previous_rfid = self.current_rfid
        self.current_rfid = target_rfid
        self.payload_written_to_card = False
        self.last_transferred_on = timezone.now()
        self.save(update_fields=["current_rfid", "payload_written_to_card", "last_transferred_on"])

        return NFTTransfer.objects.create(
            identity=self,
            from_rfid=previous_rfid,
            to_rfid=target_rfid,
            actor=actor,
        )

    def sync_payload_from_nft(self) -> None:
        """Copy NFT payload into card payload storage for upcoming card writes."""

        self.card_payload = self.nft.payload
        self.payload_written_to_card = False
        self.save(update_fields=["card_payload", "payload_written_to_card"])


class NFTTransfer(Entity):
    """Audit trail for identity transfers between RFID cards."""

    identity = models.ForeignKey(
        "nfts.RFIDBoundIdentity",
        on_delete=models.CASCADE,
        related_name="transfers",
    )
    from_rfid = models.ForeignKey(
        "cards.RFID",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nft_transfers_from",
    )
    to_rfid = models.ForeignKey(
        "cards.RFID",
        on_delete=models.PROTECT,
        related_name="nft_transfers_to",
    )
    actor = models.CharField(max_length=255, blank=True, default="")
    transferred_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-transferred_on",)


__all__ = ["NFT", "NFTTransfer", "RFIDBoundIdentity"]
