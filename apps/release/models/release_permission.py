from django.db import models


class ReleasePermission(models.Model):
    """Non-persistent anchor for release-wide custom permissions."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("can_trigger_upgrade_checks", "Can trigger upgrade checks"),
        ]
        verbose_name = "Release Permission"
        verbose_name_plural = "Release Permissions"
