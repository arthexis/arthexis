from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class TOTPDeviceSettings(Entity):
    """Per-device configuration options for authenticator enrollments."""

    device = models.OneToOneField(
        "otp_totp.TOTPDevice",
        on_delete=models.CASCADE,
        related_name="custom_settings",
    )
    issuer = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_("Label shown in authenticator apps. Leave blank to use Arthexis."),
    )
    allow_without_password = models.BooleanField(
        default=False,
        help_text=_("Allow authenticator logins to skip the password step."),
    )
    security_group = models.ForeignKey(
        "core.SecurityGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="totp_devices",
        help_text=_(
            "Share this authenticator with every user in the selected security group."
        ),
    )
    class Meta:
        verbose_name = _("Authenticator Device Setting")
        verbose_name_plural = _("Authenticator Device Settings")
