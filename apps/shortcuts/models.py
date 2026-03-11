"""Models for server/client keyboard shortcuts and clipboard pattern routing."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


_SHORTCUT_TOKEN_PATTERN = re.compile(r"^[A-Z0-9]+$")


class Shortcut(Entity):
    """A user-configurable keyboard shortcut that executes recipes."""

    class Kind(models.TextChoices):
        SERVER = "server", _("Server Shortcut")
        CLIENT = "client", _("Client Shortcut")

    display = models.CharField(max_length=120)
    key_combo = models.CharField(max_length=120, help_text=_("Example: CTRL+SHIFT+K"))
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CLIENT)
    is_active = models.BooleanField(default=True)
    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.PROTECT,
        related_name="shortcuts",
        null=True,
        blank=True,
        help_text=_("Fallback recipe when no clipboard pattern matches."),
    )
    use_clipboard_patterns = models.BooleanField(
        default=False,
        help_text=_("Evaluate clipboard patterns in ascending priority before fallback recipe."),
    )
    clipboard_output_enabled = models.BooleanField(default=False)
    keyboard_output_enabled = models.BooleanField(default=False)
    output_template = models.TextField(
        blank=True,
        help_text=_("Optional output template; supports sigils and [ARG.*] tokens."),
    )

    class Meta:
        ordering = ("display",)
        constraints = [
            models.UniqueConstraint(
                fields=("key_combo",),
                condition=models.Q(is_active=True),
                name="shortcuts_active_key_combo_unique",
            )
        ]

    def __str__(self) -> str:
        """Return a readable shortcut label."""

        return f"{self.display} ({self.key_combo})"

    @staticmethod
    def normalize_key_combo(value: str) -> str:
        """Normalize key combos and canonicalize modifier ordering."""

        tokens = [token.strip().upper() for token in str(value or "").split("+")]
        cleaned = [token for token in tokens if token]
        modifier_order = ("CTRL", "ALT", "SHIFT", "META")
        modifiers = {token for token in cleaned if token in modifier_order}
        non_modifiers = [token for token in cleaned if token not in modifier_order]
        ordered_modifiers = [token for token in modifier_order if token in modifiers]
        return "+".join([*ordered_modifiers, *non_modifiers])

    def clean(self) -> None:
        """Validate shortcut consistency and key format."""

        super().clean()
        self.key_combo = self.normalize_key_combo(self.key_combo)
        if not self.key_combo:
            raise ValidationError({"key_combo": _("Key combo is required.")})

        parts = self.key_combo.split("+")
        invalid_tokens = [token for token in parts if not _SHORTCUT_TOKEN_PATTERN.match(token)]
        if invalid_tokens:
            raise ValidationError({"key_combo": _("Unsupported key token(s): %(tokens)s") % {"tokens": ", ".join(invalid_tokens)}})

        if self.use_clipboard_patterns and self.kind != self.Kind.CLIENT:
            raise ValidationError({"use_clipboard_patterns": _("Clipboard patterns are supported only for client shortcuts.")})

        if not self.recipe_id:
            raise ValidationError({"recipe": _("A fallback recipe is required.")})


class ClipboardPattern(Entity):
    """Pattern-driven clipboard routing for a client shortcut."""

    shortcut = models.ForeignKey(
        Shortcut,
        on_delete=models.CASCADE,
        related_name="clipboard_patterns",
    )
    display = models.CharField(max_length=120)
    pattern = models.CharField(max_length=255, help_text=_("Python regex pattern."))
    priority = models.PositiveIntegerField(default=0)
    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.PROTECT,
        related_name="clipboard_shortcut_patterns",
    )
    is_active = models.BooleanField(default=True)
    clipboard_output_enabled = models.BooleanField(default=False)
    keyboard_output_enabled = models.BooleanField(default=False)
    output_template = models.TextField(blank=True)

    class Meta:
        ordering = ("priority", "pk")

    def __str__(self) -> str:
        """Return a concise pattern label."""

        return f"{self.shortcut.display}: {self.display}"

    def clean(self) -> None:
        """Validate pattern syntax and shortcut kind compatibility."""

        super().clean()
        if self.shortcut_id and self.shortcut.kind != Shortcut.Kind.CLIENT:
            raise ValidationError({"shortcut": _("Clipboard patterns require a client shortcut.")})
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValidationError({"pattern": _("Invalid regex: %(error)s") % {"error": str(exc)}}) from exc


__all__ = ["ClipboardPattern", "Shortcut"]
