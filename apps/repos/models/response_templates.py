"""Per-user GitHub response templates for issue and PR workflows."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class GitHubResponseTemplate(Entity):
    """Reusable canned response text a user can apply to GitHub discussions."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="github_response_templates",
    )
    label = models.CharField(max_length=120)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("user__username", "label")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "label"],
                name="unique_github_response_template_label_per_user",
            )
        ]
        verbose_name = _("GitHub Response Template")
        verbose_name_plural = _("GitHub Response Templates")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.user}: {self.label}"
