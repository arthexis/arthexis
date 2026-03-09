"""Models for desktop assistant extension registration and desktop shortcuts."""

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


class DesktopShortcut(Entity):
    """Represent an OS desktop shortcut that can be synchronized from Django data."""

    class LaunchMode(models.TextChoices):
        """Available target launch modes for desktop shortcuts."""

        URL = "url", "URL"
        COMMAND = "command", "Command"

    class InstallLocation(models.TextChoices):
        """Filesystem locations where launcher files should be written."""

        DESKTOP = "desktop", "Desktop"
        APPLICATIONS = "applications", "Applications menu"
        BOTH = "both", "Desktop and Applications menu"

    slug = models.SlugField(max_length=80, unique=True)
    desktop_filename = models.CharField(
        max_length=128,
        unique=True,
        help_text="Filename used in the desktop folder (without .desktop suffix).",
    )
    name = models.CharField(max_length=128)
    comment = models.CharField(max_length=255, blank=True)
    launch_mode = models.CharField(
        max_length=16,
        choices=LaunchMode.choices,
        default=LaunchMode.URL,
    )
    target_url = models.CharField(
        max_length=512,
        blank=True,
        help_text="URL to open. Supports {port} placeholder.",
    )
    command = models.CharField(
        max_length=512,
        blank=True,
        help_text="Command to execute when launch mode is command.",
    )
    install_location = models.CharField(
        max_length=16,
        choices=InstallLocation.choices,
        default=InstallLocation.DESKTOP,
        help_text="Where the .desktop launcher file should be installed.",
    )
    icon_name = models.CharField(
        max_length=128,
        blank=True,
        help_text="Existing OS icon name to reference (for example web-browser).",
    )
    icon_base64 = models.TextField(
        blank=True,
        help_text="Base64 encoded icon payload written to ~/.local/share/icons.",
    )
    icon_extension = models.CharField(
        max_length=8,
        default="png",
        help_text="Extension used when persisting base64 icon payloads.",
    )
    categories = models.CharField(max_length=255, blank=True, default="")
    terminal = models.BooleanField(default=False)
    startup_notify = models.BooleanField(default=True)
    extra_entries = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional Desktop Entry key/value pairs.",
    )
    condition_expression = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Optional Python-like expression evaluated against context keys: "
            "has_desktop_ui, has_feature, is_staff, is_superuser, group_names."
        ),
    )
    condition_command = models.CharField(
        max_length=512,
        blank=True,
        help_text="Optional shell command. Exit code 0 installs the shortcut.",
    )
    require_desktop_ui = models.BooleanField(default=True)
    required_features = models.ManyToManyField("nodes.NodeFeature", blank=True)
    required_groups = models.ManyToManyField("auth.Group", blank=True)
    only_staff = models.BooleanField(default=False)
    only_superuser = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "name", "slug")
        verbose_name = "Desktop Shortcut"
        verbose_name_plural = "Desktop Shortcuts"

    def __str__(self) -> str:
        """Return a readable label for admin interfaces."""
        return self.name

    def clean(self) -> None:
        """Validate desktop shortcut launch and icon configuration."""
        super().clean()

        if self.launch_mode == self.LaunchMode.URL and not self.target_url.strip():
            raise ValidationError({"target_url": "A target URL is required for URL mode."})
        if self.launch_mode == self.LaunchMode.COMMAND and not self.command.strip():
            raise ValidationError({"command": "A command is required for command mode."})
        if self.icon_base64 and self.icon_name:
            raise ValidationError(
                {
                    "icon_base64": (
                        "Choose either icon base64 payload or icon name, not both."
                    )
                }
            )
        if not self.desktop_filename.strip():
            raise ValidationError({"desktop_filename": "Desktop filename is required."})
        if "/" in self.desktop_filename or "\\" in self.desktop_filename:
            raise ValidationError(
                {"desktop_filename": "Desktop filename cannot contain path separators."}
            )


__all__ = ["RegisteredExtension", "DesktopShortcut"]
