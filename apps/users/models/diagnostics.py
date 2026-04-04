"""User diagnostics profiles, events, and report bundles."""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity

from .profile import Profile


class UserDiagnosticsProfile(Profile):
    """Per-owner diagnostics preferences for support and development triage."""

    is_enabled = models.BooleanField(
        default=True,
        db_default=True,
        verbose_name=_("Enabled"),
        help_text=_("Disable this profile without deleting saved preferences."),
    )
    collect_diagnostics = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Collect diagnostics"),
        help_text=_(
            "Allow Arthexis to collect request error context for bug fixing and development."
        ),
    )
    allow_manual_feedback = models.BooleanField(
        default=True,
        db_default=True,
        verbose_name=_("Allow manual feedback"),
        help_text=_("Allow staff to attach manual feedback notes to diagnostics logs."),
    )

    class Meta(Profile.Meta):
        verbose_name = _("User Diagnostics Profile")
        verbose_name_plural = _("User Diagnostics Profiles")
        constraints = [
            models.CheckConstraint(
                name="users_diag_profile_exactly_one_owner",
                condition=(
                    models.Q(user__isnull=False, group__isnull=True, avatar__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=False, avatar__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=True, avatar__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(user__isnull=False),
                name="users_diag_profile_unique_user",
            ),
            models.UniqueConstraint(
                fields=["group"],
                condition=models.Q(group__isnull=False),
                name="users_diag_profile_unique_group",
            ),
            models.UniqueConstraint(
                fields=["avatar"],
                condition=models.Q(avatar__isnull=False),
                name="users_diag_profile_unique_avatar",
            ),
        ]

    def __str__(self) -> str:
        return _("Diagnostics profile for %(owner)s") % {"owner": self.owner_display()}


class UserDiagnosticEvent(Entity):
    """Correlated diagnostic events captured from runtime errors or feedback."""

    class Source(models.TextChoices):
        ERROR = "error", _("Error")
        FEEDBACK = "feedback", _("Manual feedback")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnostic_events",
    )
    profile = models.ForeignKey(
        UserDiagnosticsProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.ERROR)
    summary = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    request_method = models.CharField(max_length=16, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = _("User Diagnostic Event")
        verbose_name_plural = _("User Diagnostic Events")

    @classmethod
    def build_fingerprint(
        cls,
        *,
        source: str,
        summary: str,
        request_method: str = "",
        request_path: str = "",
    ) -> str:
        digest = hashlib.sha256(
            "::".join(
                (
                    source or "",
                    summary or "",
                    request_method.upper(),
                    request_path or "",
                )
            ).encode("utf-8")
        )
        return digest.hexdigest()

    def __str__(self) -> str:
        username = getattr(self.user, "username", None) or _("unknown user")
        return f"{username}: {self.summary}"


class UserDiagnosticBundle(Entity):
    """Bundle grouped events for easier patch-request submission."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnostic_bundles",
    )
    profile = models.ForeignKey(
        UserDiagnosticsProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bundles",
    )
    title = models.CharField(max_length=200)
    report = models.TextField(
        blank=True,
        help_text=_("Human-readable report bundle text to copy into a patch request."),
    )
    events = models.ManyToManyField(UserDiagnosticEvent, related_name="bundles", blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("User Diagnostic Bundle")
        verbose_name_plural = _("User Diagnostic Bundles")

    def __str__(self) -> str:
        return self.title
