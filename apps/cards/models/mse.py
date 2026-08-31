from __future__ import annotations

from pathlib import Path

from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.cards import mse


class CardSet(Entity):
    """Metadata for an MSE card set import."""

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True, default="")
    language = models.CharField(max_length=32, blank=True, default="")
    game = models.CharField(max_length=255, blank=True, default="")
    style = models.CharField(max_length=255, blank=True, default="")
    set_info = models.JSONField(default=dict, blank=True)
    style_settings = models.JSONField(default=dict, blank=True)
    raw_set_text = models.TextField(blank=True, default="")
    source_filename = models.CharField(max_length=255, blank=True, default="")
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Card Set")
        verbose_name_plural = _("Card Sets")
        ordering = ("-created_on", "name")

    def __str__(self) -> str:  # pragma: no cover - representational
        return self.name

    @classmethod
    def create_from_upload(cls, uploaded_file) -> "CardSet":
        filename = getattr(uploaded_file, "name", "") or ""
        payload = uploaded_file.read()
        set_text = mse.extract_set_text(payload)
        parsed = mse.parse_mse_set(set_text)
        return cls.create_from_parsed(parsed, set_text, filename=filename)

    @classmethod
    def create_from_parsed(
        cls,
        parsed: dict[str, object],
        set_text: str,
        *,
        filename: str = "",
    ) -> "CardSet":
        meta = mse.extract_set_metadata(parsed)
        default_name = Path(filename).stem if filename else _("Imported Card Set")
        name = mse.extract_set_name(parsed, default=str(default_name))
        code = mse.extract_set_code(parsed)
        language = mse.extract_set_language(parsed)
        cards = mse.extract_cards(parsed)

        with transaction.atomic():
            card_set = cls.objects.create(
                name=name,
                code=code,
                language=language,
                game=meta.get("game", "") or "",
                style=meta.get("style", "") or "",
                set_info=meta.get("set_info", {}) or {},
                style_settings=meta.get("style_settings", {}) or {},
                raw_set_text=set_text,
                source_filename=filename or "",
            )
            designs = []
            for index, card in enumerate(cards, start=1):
                if not isinstance(card, dict):
                    continue
                design_name = mse.extract_card_name(card, default=f"Card {index}")
                designs.append(
                    CardDesign(
                        card_set=card_set,
                        name=design_name,
                        sequence=index,
                        fields=card,
                    )
                )
            if designs:
                CardDesign.objects.bulk_create(designs)
        return card_set


class CardDesign(Entity):
    """Card design metadata derived from an MSE set."""

    card_set = models.ForeignKey(
        CardSet,
        on_delete=models.CASCADE,
        related_name="card_designs",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    sequence = models.PositiveIntegerField(default=0)
    fields = models.JSONField(default=dict, blank=True)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Card Design")
        verbose_name_plural = _("Card Designs")
        ordering = ("card_set", "sequence", "name")

    def __str__(self) -> str:  # pragma: no cover - representational
        if self.name:
            return self.name
        return f"{self.card_set} #{self.sequence}"

