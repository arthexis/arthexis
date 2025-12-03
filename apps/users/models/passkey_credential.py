from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class PasskeyCredential(Entity):
    """Stored WebAuthn credentials that allow passwordless logins."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="passkeys",
    )
    name = models.CharField(
        max_length=80,
        help_text=_("Friendly label shown on the security settings page."),
    )
    credential_id = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("Base64-encoded identifier returned by the authenticator."),
    )
    public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    user_handle = models.CharField(max_length=255)
    transports = models.JSONField(default=list, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "created_at")
        verbose_name = _("Passkey")
        verbose_name_plural = _("Passkeys")
        db_table = "core_passkeycredential"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "name"),
                name="core_passkey_unique_name_per_user",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - human-readable representation
        return f"{self.name} ({self.user})"
