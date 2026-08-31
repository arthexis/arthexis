from __future__ import annotations

from django.db import models

from apps.base.models import Entity

from .utils import NameRepresentationMixin


class Platform(NameRepresentationMixin, Entity):
    """Supported hardware and operating system combinations."""

    name = models.CharField(max_length=100, unique=True)
    hardware = models.CharField(max_length=100)
    architecture = models.CharField(max_length=50, blank=True)
    os_name = models.CharField(max_length=100)
    os_version = models.CharField(max_length=50)

    class Meta:
        ordering = ["name"]
        verbose_name = "Platform"
        verbose_name_plural = "Platforms"
        constraints = [
            models.UniqueConstraint(
                fields=["hardware", "architecture", "os_name", "os_version"],
                name="nodes_platform_hardware_os_unique",
            )
        ]
