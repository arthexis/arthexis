"""Models for server/client keyboard shortcuts and clipboard pattern routing."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


_SHORTCUT_TOKEN_PATTERN = re.compile(r"^[A-Z0-9]+$")
_TARGET_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class ShortcutTargetKind(models.TextChoices):
    """Supported non-programmable shortcut target categories."""

    ACTION = "action", _("Predefined Action")
    COMMAND = "command", _("Structured Command")
    WORKFLOW = "workflow", _("Workflow")


class Shortcut(Entity):
    """A user-configurable keyboard shortcut that executes typed targets."""

    class Kind(models.TextChoices):
        SERVER = "server", _("Server Shortcut")
        CLIENT = "client", _("Client Shortcut")

    display = models.CharField(max_length=120)
    key_combo = models.CharField(max_length=120, help_text=_("Example: CTRL+SHIFT+K"))
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CLIENT)
    is_active = models.BooleanField(default=True)
    target_kind = models.CharField(
        max_length=24,
        choices=ShortcutTargetKind.choices,
        default=ShortcutTargetKind.ACTION,
        help_text=_("Typed execution target used when no clipboard pattern matches."),
    )
    target_identifier = models.CharField(
        max_length=120,
        help_text=_("Structured action, command, or workflow identifier."),
    )
    target_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Validated target parameters stored as structured JSON."),
    )
    use_clipboard_patterns = models.BooleanField(
        default=False,
        help_text=_("Evaluate clipboard patterns in ascending priority before fallback target."),
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

    @staticmethod
    def validate_target_fields(*, kind: str, identifier: str, payload: object, field_prefix: str = "target") -> None:
        """Validate stored target metadata before runtime execution.

        Parameters:
            kind: Target kind stored on the model instance.
            identifier: Structured identifier for the target.
            payload: JSON-compatible payload with parameters.
            field_prefix: Prefix used when mapping errors to model fields.

        Returns:
            None.

        Raises:
            ValidationError: If any target field is malformed.
        """

        errors: dict[str, str] = {}
        normalized_identifier = str(identifier or "").strip()
        if kind not in ShortcutTargetKind.values:
            errors[f"{field_prefix}_kind"] = _("Unsupported target kind.")
        if not normalized_identifier:
            errors[f"{field_prefix}_identifier"] = _("A target identifier is required.")
        elif not _TARGET_IDENTIFIER_PATTERN.match(normalized_identifier):
            errors[f"{field_prefix}_identifier"] = _("Target identifier contains unsupported characters.")
        if not isinstance(payload, dict):
            errors[f"{field_prefix}_payload"] = _("Target parameters must be a JSON object.")
        if errors:
            raise ValidationError(errors)

    def clean(self) -> None:
        """Validate shortcut consistency and key format."""

        super().clean()
        self.key_combo = self.normalize_key_combo(self.key_combo)
        if self.target_payload in (None, ""):
            self.target_payload = {}
        if not self.key_combo:
            raise ValidationError({"key_combo": _("Key combo is required.")})

        parts = self.key_combo.split("+")
        invalid_tokens = [token for token in parts if not _SHORTCUT_TOKEN_PATTERN.match(token)]
        if invalid_tokens:
            raise ValidationError({"key_combo": _("Unsupported key token(s): %(tokens)s") % {"tokens": ", ".join(invalid_tokens)}})

        if self.use_clipboard_patterns and self.kind != self.Kind.CLIENT:
            raise ValidationError({"use_clipboard_patterns": _("Clipboard patterns are supported only for client shortcuts.")})

        self.validate_target_fields(
            kind=self.target_kind,
            identifier=self.target_identifier,
            payload=self.target_payload,
        )


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
    target_kind = models.CharField(
        max_length=24,
        choices=ShortcutTargetKind.choices,
        default=ShortcutTargetKind.ACTION,
    )
    target_identifier = models.CharField(max_length=120)
    target_payload = models.JSONField(default=dict, blank=True)
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
        if self.target_payload in (None, ""):
            self.target_payload = {}
        if self.shortcut_id and self.shortcut.kind != Shortcut.Kind.CLIENT:
            raise ValidationError({"shortcut": _("Clipboard patterns require a client shortcut.")})
        Shortcut.validate_target_fields(
            kind=self.target_kind,
            identifier=self.target_identifier,
            payload=self.target_payload,
        )
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValidationError({"pattern": _("Invalid regex: %(error)s") % {"error": str(exc)}}) from exc


__all__ = ["ClipboardPattern", "Shortcut", "ShortcutTargetKind"]
