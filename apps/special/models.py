"""Persistent definitions for safe, introspectable special commands."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

_SPECIAL_WORD_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SpecialCommand(models.Model):
    """DB-backed command definition used for runtime validation and invocation."""

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Singular management command name (single lowercase word).",
    )
    plural_name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Plural command alias (single lowercase word).",
    )
    command_name = models.CharField(
        max_length=64,
        help_text="Actual Django management command name used for invocation.",
    )
    command_path = models.CharField(
        max_length=255,
        help_text="Import path to the command class for introspection and traceability.",
    )
    keystone_model = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional app_label.ModelName keystone model reference.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Special command"
        verbose_name_plural = "Special commands"

    def __str__(self) -> str:
        """Return admin-friendly command label."""

        return f"{self.name}/{self.plural_name}"

    def clean(self) -> None:
        """Validate naming restrictions and optional keystone target format."""

        super().clean()

        self.name = (self.name or "").strip()
        self.plural_name = (self.plural_name or "").strip()
        self.command_name = (self.command_name or "").strip()

        for field in ("name", "plural_name", "command_name"):
            value = getattr(self, field)
            if not _SPECIAL_WORD_RE.fullmatch(value):
                raise ValidationError(
                    {field: "Special command names must be one lowercase word."}
                )

        if self.name == self.plural_name:
            raise ValidationError(
                {"plural_name": "Plural form must differ from singular command name."}
            )

        self.keystone_model = (self.keystone_model or "").strip()
        parts = self.keystone_model.split(".") if self.keystone_model else []
        if self.keystone_model and (len(parts) != 2 or not all(parts)):
            raise ValidationError(
                {
                    "keystone_model": (
                        "Keystone model must use app_label.ModelName format when set."
                    )
                }
            )

        collisions = SpecialCommand.objects.filter(
            Q(name__iexact=self.plural_name) | Q(plural_name__iexact=self.name)
        )
        if self.pk:
            collisions = collisions.exclude(pk=self.pk)
        if collisions.exists():
            raise ValidationError(
                {
                    "name": "Special command names and plural aliases must be globally unique."
                }
            )

    def save(self, *args, **kwargs):
        """Validate the model before persisting it."""

        self.full_clean()
        return super().save(*args, **kwargs)


class SpecialCommandParameter(models.Model):
    """Introspected CLI parameter metadata for a special command."""

    class ParameterKind(models.TextChoices):
        """Supported CLI parameter classes."""

        POSITIONAL = "positional", "Positional"
        OPTION = "option", "Option"
        FLAG = "flag", "Flag"

    class ValueType(models.TextChoices):
        """Supported normalized value types for safe validation."""

        STRING = "string", "String"
        INTEGER = "integer", "Integer"
        FLOAT = "float", "Float"
        BOOLEAN = "boolean", "Boolean"

    command = models.ForeignKey(
        SpecialCommand,
        related_name="parameters",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=64)
    cli_name = models.CharField(
        max_length=64,
        help_text="Argument flag (e.g. --enabled) or positional name.",
    )
    kind = models.CharField(max_length=16, choices=ParameterKind.choices)
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    is_required = models.BooleanField(default=False)
    allows_multiple = models.BooleanField(default=False)
    choices = models.JSONField(default=list, blank=True)
    help_text = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("command", "sort_order", "id")
        unique_together = (("command", "name"), ("command", "cli_name"))
        verbose_name = "Special command parameter"
        verbose_name_plural = "Special command parameters"

    def __str__(self) -> str:
        """Return admin-friendly parameter label."""

        return f"{self.command.name}:{self.cli_name}"

    def clean(self) -> None:
        """Enforce parameter naming and flag safety constraints."""

        super().clean()

        normalized_name = (self.name or "").strip()
        self.name = normalized_name
        if not _SPECIAL_WORD_RE.fullmatch(normalized_name):
            raise ValidationError(
                {"name": "Parameter name must be one lowercase word."}
            )

        if self.kind == self.ParameterKind.POSITIONAL:
            normalized_cli_name = (self.cli_name or "").strip()
            self.cli_name = normalized_cli_name
            if not _SPECIAL_WORD_RE.fullmatch(normalized_cli_name):
                raise ValidationError(
                    {
                        "cli_name": "Positional argument names must be one lowercase word."
                    }
                )
            return

        normalized_cli_name = (self.cli_name or "").strip()
        self.cli_name = normalized_cli_name
        if (
            not normalized_cli_name.startswith("--")
            or len(normalized_cli_name) <= 2
            or not _SPECIAL_WORD_RE.fullmatch(normalized_cli_name[2:])
        ):
            raise ValidationError({"cli_name": "Option/flag names must start with --."})
