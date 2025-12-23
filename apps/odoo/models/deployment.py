from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class OdooDeployment(Entity):
    """Representation of a local Odoo installation and its database settings."""

    name = models.CharField(max_length=100, blank=True)
    config_path = models.CharField(
        max_length=500,
        unique=True,
        help_text=_("Absolute path to the odoo configuration file."),
    )
    base_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=_("Directory that contains the odoo configuration file."),
    )
    addons_path = models.TextField(blank=True)
    data_dir = models.CharField(max_length=500, blank=True)
    db_host = models.CharField(max_length=255, blank=True)
    db_port = models.PositiveIntegerField(null=True, blank=True)
    db_user = models.CharField(max_length=255, blank=True)
    db_password = models.CharField(max_length=255, blank=True)
    db_name = models.CharField(max_length=255, blank=True)
    db_filter = models.CharField(max_length=255, blank=True)
    admin_password = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Database operations master password."),
    )
    http_port = models.PositiveIntegerField(null=True, blank=True)
    longpolling_port = models.PositiveIntegerField(null=True, blank=True)
    logfile = models.CharField(max_length=500, blank=True)
    last_discovered = models.DateTimeField(null=True, blank=True)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name or self.config_path

    class Meta:
        ordering = ("name", "config_path")
        verbose_name = _("Odoo Deployment")
        verbose_name_plural = _("Odoo Deployments")
        db_table = "core_odoodeployment"
