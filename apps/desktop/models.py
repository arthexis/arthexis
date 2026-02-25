"""Models for desktop assistant extension registration."""

from __future__ import annotations

import shlex

from django.core.exceptions import ValidationError
from django.db import models

from apps.base.models import Entity


class RegisteredExtension(Entity):
    """Map a file extension to a Django management command executable."""

    extension = models.CharField(
        max_length=32,
        unique=True,
        help_text="Operating system extension including dot (for example: .pdf).",
    )
    description = models.TextField(
        blank=True,
        help_text=(
            "Administrative notes. Use filename sigil in command args to inject "
            "the opened file path."
        ),
    )
    django_command = models.CharField(
        max_length=128,
        help_text="Django management command to execute.",
    )
    extra_args = models.TextField(
        blank=True,
        help_text=(
            "Optional arguments (shell-style) for the command. The filename sigil "
            "will be replaced with the opened file path."
        ),
    )
    filename_sigil = models.CharField(
        max_length=64,
        default="{filename}",
        help_text="Sigil token replaced with the opened file path in extra args.",
    )
    filename_as_input = models.BooleanField(
        default=False,
        help_text=(
            "Pass the opened file path as command stdin instead of replacing the "
            "filename sigil."
        ),
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("extension",)
        verbose_name = "Registered Extension"
        verbose_name_plural = "Registered Extensions"

    def __str__(self) -> str:
        """Return the registered file extension."""
        return self.extension

    def clean(self) -> None:
        """Validate extension and execution settings."""
        super().clean()

        if not self.extension.startswith("."):
            raise ValidationError({"extension": "Extension must start with a dot."})

        if any(char in self.extension for char in (" ", "/", "\\")):
            raise ValidationError(
                {
                    "extension": (
                        "Extension cannot include spaces or path separators."
                    )
                }
            )

        if not self.django_command.strip():
            raise ValidationError({"django_command": "Command name is required."})

        if not self.filename_as_input and not self.filename_sigil.strip():
            raise ValidationError(
                {"filename_sigil": "Filename sigil cannot be empty."}
            )

    def build_runtime_command(self, filename: str | None = None) -> tuple[list[str], str | None]:
        """Build command arguments and optional input payload for execution."""

        parsed_args = shlex.split(self.extra_args) if self.extra_args else []
        if filename and not self.filename_as_input:
            parsed_args = [arg.replace(self.filename_sigil, filename) for arg in parsed_args]
        input_data = filename if (filename and self.filename_as_input) else None
        return [self.django_command, *parsed_args], input_data


__all__ = ["RegisteredExtension"]
