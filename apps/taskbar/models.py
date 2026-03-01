"""Models for taskbar menus, actions, and icon configuration."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from apps.base.models import Entity


class TaskbarMenu(Entity):
    """Named menu rendered by taskbar-capable desktop integrations."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    left_click_enabled = models.BooleanField(default=True)
    right_click_default_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Taskbar Menu"
        verbose_name_plural = "Taskbar Menus"

    def __str__(self) -> str:
        """Return display label for admin and shell output."""

        return self.name


class TaskbarMenuAction(Entity):
    """An invokable command or recipe entry displayed in a taskbar menu."""

    class ActionType(models.TextChoices):
        """Supported action target types."""

        COMMAND = "command", "Command"
        RECIPE = "recipe", "Recipe"

    menu = models.ForeignKey(
        TaskbarMenu,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    label = models.CharField(max_length=120)
    action_type = models.CharField(
        max_length=16,
        choices=ActionType.choices,
        default=ActionType.COMMAND,
    )
    command = models.CharField(max_length=200, blank=True)
    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="taskbar_actions",
    )
    is_left_click = models.BooleanField(
        default=True,
        help_text="When true this entry appears in the left-click menu payload.",
    )
    is_default_right_click = models.BooleanField(
        default=False,
        help_text="When true this action is used as the right-click default action.",
    )

    class Meta:
        ordering = ("menu", "label")
        constraints = [
            models.UniqueConstraint(
                fields=("menu",),
                condition=models.Q(is_default_right_click=True),
                name="taskbar_one_default_right_click_per_menu",
            )
        ]

    def __str__(self) -> str:
        """Return display label for admin and shell output."""

        return f"{self.menu.name}: {self.label}"

    def clean(self) -> None:
        """Validate command and recipe assignment combinations."""

        super().clean()

        if self.action_type == self.ActionType.COMMAND:
            if not self.command.strip():
                raise ValidationError({"command": "A command is required for command actions."})
            if self.recipe_id:
                raise ValidationError({"recipe": "Recipe must be empty for command actions."})
        elif self.action_type == self.ActionType.RECIPE:
            if not self.recipe_id:
                raise ValidationError({"recipe": "Recipe is required for recipe actions."})
            if self.command.strip():
                raise ValidationError({"command": "Command must be empty for recipe actions."})


class TaskbarIcon(Entity):
    """Taskbar icon payload encoded in base64 for easy cross-platform storage."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    icon_b64 = models.TextField(help_text="Base64 PNG payload without data URI prefix.")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("is_default",),
                condition=models.Q(is_default=True),
                name="taskbar_single_default_icon",
            )
        ]

    def __str__(self) -> str:
        """Return display label for admin and shell output."""

        return self.name


__all__ = ["TaskbarMenu", "TaskbarMenuAction", "TaskbarIcon"]
