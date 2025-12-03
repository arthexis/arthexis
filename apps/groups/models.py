from django.contrib.auth.models import Group
from django.db import models
from django.utils.translation import gettext_lazy as _


class SecurityGroup(Group):
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    site_template = models.ForeignKey(
        "pages.SiteTemplate",
        on_delete=models.SET_NULL,
        related_name="security_groups",
        null=True,
        blank=True,
        verbose_name=_("Site template"),
        help_text=_("Branding template applied to members of this group when set."),
    )

    class Meta:
        verbose_name = "Security Group"
        verbose_name_plural = "Security Groups"
        db_table = "core_securitygroup"
