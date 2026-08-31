"""Models for storing GitHub tokens."""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable
from apps.sigils.fields import SigilShortAutoField


class GitHubToken(Ownable):
    """Store a GitHub token for a user."""

    token = SigilShortAutoField(
        max_length=255,
        help_text=_("Personal access token or OAuth token for GitHub operations."),
    )
    label = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _("GitHub Token")
        verbose_name_plural = _("GitHub Tokens")
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(user__isnull=False, group__isnull=True),
                name="unique_github_token_user",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.label or _("GitHub Token")
