"""Models for Magic Set Editor card sets and designs."""
from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket

CARD_SET_BUCKET_SLUG = "cards-mse-sets"
CARD_SET_ALLOWED_PATTERNS = "\n".join(["*.mse-set", "*.zip"])


def get_cardset_bucket():
    return ensure_media_bucket(
        slug=CARD_SET_BUCKET_SLUG,
        name="Card Set Uploads",
        allowed_patterns=CARD_SET_ALLOWED_PATTERNS,
        max_bytes=25 * 1024 * 1024,
    )


class CardSet(Entity):
    """An imported Magic Set Editor card set."""

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True, default="")
    game = models.CharField(max_length=255, blank=True, default="")
    style = models.CharField(max_length=255, blank=True, default="")
    language = models.CharField(max_length=64, blank=True, default="")
    set_info = models.JSONField(blank=True, default=dict)
    style_settings = models.JSONField(blank=True, default=dict)
    raw_data = models.TextField(blank=True, default="")
    source_media = models.ForeignKey(
        MediaFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="card_sets",
        verbose_name=_("Set file"),
    )

    class Meta:
        verbose_name = _("Card Set")
        verbose_name_plural = _("Card Sets")
        ordering = ("name",)

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.name

    def replace_card_designs(self, cards: list[dict]):
        CardDesign.objects.filter(card_set=self).delete()
        if not cards:
            return
        designs = []
        for index, payload in enumerate(cards, start=1):
            title = payload.get("name") or payload.get("title") or payload.get("card name") or ""
            designs.append(
                CardDesign(
                    card_set=self,
                    name=title or f"Card {index}",
                    sequence=index,
                    fields=payload,
                )
            )
        CardDesign.objects.bulk_create(designs)


class CardDesign(Entity):
    """A single card entry within an MSE set."""

    card_set = models.ForeignKey(CardSet, on_delete=models.CASCADE, related_name="card_designs")
    name = models.CharField(max_length=255, blank=True, default="")
    sequence = models.PositiveIntegerField(default=0)
    fields = models.JSONField(blank=True, default=dict)

    class Meta:
        verbose_name = _("Card Design")
        verbose_name_plural = _("Card Designs")
        ordering = ("card_set", "sequence")

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.name or f"Card {self.sequence}"


__all__ = ["CardDesign", "CardSet", "get_cardset_bucket"]
