"""Models for supported dashboard actions and staff tasks."""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.actions.internal_actions import (
    get_internal_action_choices,
    get_internal_action_spec,
    resolve_internal_action_url,
)


class DashboardAction(models.Model):
    """Declarative dashboard link bound to a named internal action."""

    action_name = models.CharField(
        max_length=50,
        choices=get_internal_action_choices(),
        default="config",
        help_text=_("Named internal action rendered for this model row."),
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="dashboard_actions",
        help_text=_("Model row where this action appears on the admin dashboard."),
    )
    slug = models.SlugField(max_length=100)
    label = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "slug")
        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "slug"),
                name="actions_dashboardaction_unique_slug_per_model",
            )
        ]
        verbose_name = _("Dashboard Action")
        verbose_name_plural = _("Dashboard Actions")

    def __str__(self) -> str:
        """Return the UI label used for this dashboard action."""

        return self.display_label

    @property
    def display_label(self) -> str:
        """Return the configured label or the registry default for this action.

        Returns:
            The custom label when present, otherwise the internal action label.
        """

        spec = get_internal_action_spec(self.action_name)
        return self.label or (spec.label if spec else self.slug)

    def clean(self) -> None:
        """Validate that the selected internal action exists.

        Raises:
            ValidationError: When ``action_name`` is not registered.
        """

        super().clean()
        if get_internal_action_spec(self.action_name) is None:
            raise ValidationError({"action_name": _("Select a supported internal action.")})

    def resolve_url(self) -> str:
        """Return the resolved URL for the configured internal action.

        Returns:
            The reversed internal URL, or an empty string when unavailable.
        """

        return resolve_internal_action_url(self.action_name)

    def as_rendered_action(self) -> dict[str, str | bool]:
        """Return a template-friendly payload for dashboard row rendering.

        Returns:
            Mapping consumed by admin templates.
        """

        spec = get_internal_action_spec(self.action_name)
        return {
            "url": self.resolve_url(),
            "label": self.display_label,
            "method": spec.method if spec else "get",
            "is_discover": bool(spec and spec.is_discover),
        }


class StaffTask(models.Model):
    """Configurable dashboard task shown as a top admin button."""

    slug = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    action_name = models.CharField(max_length=50, choices=get_internal_action_choices(), default="config")
    order = models.PositiveIntegerField(default=0)
    default_enabled = models.BooleanField(default=True)
    staff_only = models.BooleanField(default=True)
    superuser_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "label")
        verbose_name = _("Task Panel")
        verbose_name_plural = _("Task Panels")

    def __str__(self) -> str:
        """Return the display label used in admin controls."""

        return self.label

    def resolve_url(self) -> str:
        """Return the resolved URL for this staff task.

        Returns:
            The reversed URL for the configured internal action, or an empty string.
        """

        return resolve_internal_action_url(self.action_name)


class StaffTaskPreference(models.Model):
    """Per-user visibility override for a staff task dashboard button."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_task_preferences",
    )
    task = models.ForeignKey(
        StaffTask,
        on_delete=models.CASCADE,
        related_name="user_preferences",
    )
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("task__order", "task__label")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "task"),
                name="actions_stafftaskpreference_unique_user_task",
            )
        ]
        verbose_name = _("Task Panel Preference")
        verbose_name_plural = _("Task Panel Preferences")

    def __str__(self) -> str:
        """Return a readable preference description."""

        return f"{self.user} · {self.task}"
