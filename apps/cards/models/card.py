"""Card-related models for the cards app."""
from __future__ import annotations

import base64
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import File
from django.db import models
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageDraw, ImageFont

from apps.base.models import Entity

__all__ = ["CardFace"]


_FONT_EXTENSIONS = ("*.ttf", "*.otf", "*.ttc")
_FONT_ROOTS = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
)


@lru_cache(maxsize=1)
def _system_fonts() -> list[tuple[str, str]]:
    fonts: dict[str, str] = {}
    for root in _FONT_ROOTS:
        if not root.exists():
            continue
        for pattern in _FONT_EXTENSIONS:
            for font_path in root.rglob(pattern):
                label = font_path.stem.replace("_", " ")
                fonts[str(font_path)] = label
    choices = [(path, label) for path, label in sorted(fonts.items(), key=lambda item: item[1].lower())]
    return choices


class CardFace(Entity):
    """Definition for printable card faces with overlays."""

    BACKGROUND_MAX_BYTES = 3 * 1024 * 1024
    ALLOWED_MODES = {"1", "L", "CMYK"}
    SIGIL_PATTERN = re.compile(r"\[([^\[\]]+)\]")

    name = models.CharField(max_length=128)
    background = models.ImageField(upload_to="cards/faces/backgrounds/")

    overlay_one_text = models.TextField(blank=True, default="", help_text=_("Primary overlay text."))
    overlay_one_font = models.CharField(max_length=255, blank=True, default="")
    overlay_one_font_size = models.PositiveIntegerField(default=28)
    overlay_one_x = models.IntegerField(default=0)
    overlay_one_y = models.IntegerField(default=0)

    overlay_two_text = models.TextField(blank=True, default="", help_text=_("Secondary overlay text."))
    overlay_two_font = models.CharField(max_length=255, blank=True, default="")
    overlay_two_font_size = models.PositiveIntegerField(default=24)
    overlay_two_x = models.IntegerField(default=0)
    overlay_two_y = models.IntegerField(default=0)

    fixed_back = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fixed_front",
        help_text=_("Optional fixed back for this card face."),
    )

    class Meta:
        verbose_name = _("Card Face")
        verbose_name_plural = _("Card Faces")
        ordering = ("name",)

    def __str__(self) -> str:  # pragma: no cover - representational
        return self.name

    @classmethod
    def font_choices(cls) -> list[tuple[str, str]]:
        choices = _system_fonts()
        return [("", _("Built-in default"))] + choices

    @classmethod
    def sigil_field_name(cls, token: str) -> str:
        sanitized = re.sub(r"[^0-9a-z]+", "_", token.lower()).strip("_")
        return f"sigil_{sanitized}" if sanitized else "sigil"

    @classmethod
    def collect_sigils(cls, *texts: str) -> list[str]:
        tokens: set[str] = set()
        for text in texts:
            if not text:
                continue
            tokens.update(match.group(1) for match in cls.SIGIL_PATTERN.finditer(str(text)))
        return sorted(tokens)

    @staticmethod
    def _resolve_token(token: str, *, current=None, overrides: dict[str, str] | None = None) -> str:
        overrides = {k.lower(): v for k, v in (overrides or {}).items()}
        override = overrides.get(token.lower())
        if override is not None:
            return str(override)
        from apps.sigils import sigil_resolver

        resolved = sigil_resolver._resolve_token(token, current)  # type: ignore[attr-defined]
        if resolved == f"[{token}]":
            return f"[{token.lower()}]"
        return resolved

    @classmethod
    def resolve_text(
        cls, text: str, *, current=None, overrides: dict[str, str] | None = None
    ) -> str:
        if not text:
            return ""

        def repl(match: re.Match[str]) -> str:
            token = match.group(1)
            return cls._resolve_token(token, current=current, overrides=overrides)

        return cls.SIGIL_PATTERN.sub(repl, text)

    def clean(self):
        super().clean()
        self._validate_background()
        if self.fixed_back_id and self.fixed_back_id == self.pk:
            raise ValidationError({"fixed_back": _("A card face cannot be its own back.")})

    def save(self, *args, **kwargs):
        previous_back_id = None
        if self.pk:
            previous_back_id = type(self).all_objects.filter(pk=self.pk).values_list("fixed_back_id", flat=True).first()
        super().save(*args, **kwargs)

        if previous_back_id and previous_back_id != self.fixed_back_id:
            previous = type(self).objects.filter(pk=previous_back_id).first()
            if previous and previous.fixed_back_id == self.pk:
                previous.fixed_back = None
                previous.save(update_fields=["fixed_back"])

        if self.fixed_back_id:
            counterpart = self.fixed_back
            if counterpart and counterpart.fixed_back_id != self.pk:
                counterpart.fixed_back = self
                counterpart.save(update_fields=["fixed_back"])

    def _validate_background(self):
        if not self.background:
            raise ValidationError({"background": _("A background image is required.")})
        file: File = self.background.file  # type: ignore[attr-defined]
        if file.size and file.size > self.BACKGROUND_MAX_BYTES:
            raise ValidationError(
                {
                    "background": _(
                        "Background image exceeds the maximum printable size of %(size)s bytes."
                    )
                    % {"size": self.BACKGROUND_MAX_BYTES}
                }
            )
        try:
            file.seek(0)
            with Image.open(file) as image:
                image.verify()
            file.seek(0)
            with Image.open(file) as image:
                mode = image.mode
                if mode not in self.ALLOWED_MODES:
                    raise ValidationError(
                        {
                            "background": _(
                                "Background must be monochrome or CMYK (current mode: %(mode)s)."
                            )
                            % {"mode": mode}
                        }
                    )
        except ValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ValidationError({"background": _(f"Invalid background image: {exc}")}) from exc

    def _load_font(self, path: str, size: int):
        if path:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                pass
        return ImageFont.load_default()

    def render_preview(
        self,
        *,
        overlay_one_text: str,
        overlay_two_text: str,
        overlay_one_font: str,
        overlay_two_font: str,
        overlay_one_size: int,
        overlay_two_size: int,
        overlay_one_position: tuple[int, int],
        overlay_two_position: tuple[int, int],
    ) -> str:
        if not self.background:
            return ""
        self.background.file.seek(0)  # type: ignore[attr-defined]
        with Image.open(self.background) as base:
            canvas = base.convert("RGBA")
            draw = ImageDraw.Draw(canvas)
            if overlay_one_text:
                font_one = self._load_font(overlay_one_font, overlay_one_size)
                draw.text(overlay_one_position, overlay_one_text, font=font_one, fill="black")
            if overlay_two_text:
                font_two = self._load_font(overlay_two_font, overlay_two_size)
                draw.text(overlay_two_position, overlay_two_text, font=font_two, fill="black")
            buffer = BytesIO()
            canvas.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
