from django.contrib.auth.models import Group
from django.db import models
from django.utils.translation import gettext_lazy as _

from .constants import STAFF_SECURITY_GROUP_NAMES


class SecurityGroup(Group):
    """Staff-facing security group model shared across the suite."""

    app = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("App"),
        help_text=_("Owning app label for this security group."),
    )
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

    @property
    def is_canonical_staff_group(self) -> bool:
        """Return whether this group is one of the five canonical staff groups."""

        return self.name in STAFF_SECURITY_GROUP_NAMES

    @property
    def security_model_label(self) -> str:
        """Return a short label describing the group's place in the security model."""

        if self.is_canonical_staff_group:
            return _("Canonical staff security group")
        return _("Context-specific security group")
