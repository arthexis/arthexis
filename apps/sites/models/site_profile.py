from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class SiteProfile(models.Model):
    site = models.OneToOneField(
        "sites.Site",
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("Site"),
    )
    default_landing = models.ForeignKey(
        "pages.Landing",
        on_delete=models.SET_NULL,
        related_name="default_for_site_profiles",
        null=True,
        blank=True,
        verbose_name=_("Default landing"),
        help_text=_("Landing visitors should be redirected to by default."),
        limit_choices_to={"is_deleted": False, "enabled": True},
    )
    interface_landing = models.ForeignKey(
        "pages.Landing",
        on_delete=models.SET_NULL,
        related_name="interface_for_site_profiles",
        null=True,
        blank=True,
        verbose_name=_("Interface landing"),
        help_text=_(
            "Landing visitors should be redirected to when the Operator Site Interface suite feature is disabled."
        ),
        limit_choices_to={"is_deleted": False, "enabled": True},
    )
    template = models.ForeignKey(
        "pages.SiteTemplate",
        on_delete=models.SET_NULL,
        related_name="site_profiles",
        null=True,
        blank=True,
        verbose_name=_("Template"),
    )
    enable_public_chat = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Enable public chat"),
        help_text=_(
            "Allow the chat button for all visitors on this site, including guests."
        ),
    )
    managed = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Managed by local NGINX"),
        help_text=_("Include this site when staging the local NGINX configuration."),
    )
    require_https = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Require HTTPS"),
        help_text=_(
            "Redirect HTTP traffic to HTTPS when the staged NGINX configuration is applied."
        ),
    )

    class Meta:
        verbose_name = _("Site profile")
        verbose_name_plural = _("Site profiles")

    def __str__(self) -> str:
        return f"{self.site.domain}"
