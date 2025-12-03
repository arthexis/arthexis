from django.db import models

from apps.base.models import Entity


class Transition(Entity):
    """Persist workflow transitions to reconstruct state timelines."""

    workflow = models.CharField(
        max_length=255,
        help_text="Workflow name for the transitioning instance.",
    )
    identifier = models.CharField(
        max_length=255,
        help_text="Identifier for the workflow instance (for example, a primary key).",
    )
    from_state = models.CharField(
        max_length=255,
        blank=True,
        help_text="State before running the step.",
    )
    to_state = models.CharField(
        max_length=255,
        help_text="State after completing the step.",
    )
    step_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable name of the executed step.",
    )
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["workflow", "identifier", "occurred_at"]),
        ]
        ordering = ["-occurred_at", "-id"]


__all__ = ["Transition"]
