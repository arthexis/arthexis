from __future__ import annotations

import re

from django.apps import apps as django_apps
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class ApplicationManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Application(Entity):
    name = models.CharField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(blank=True, null=True)

    objects = ApplicationManager()

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display_name

    @property
    def installed(self) -> bool:
        name = (self.name or "").strip()
        if not name:
            return False

        if django_apps.is_installed(name):
            return True

        for config in django_apps.get_app_configs():
            if config.label == name:
                return True
            if config.name == name or config.name.endswith(f".{name}"):
                return True

        return False

    @property
    def verbose_name(self) -> str:
        try:
            return django_apps.get_app_config(self.name).verbose_name
        except LookupError:
            return self.name

    @property
    def display_name(self) -> str:
        formatted_name = self.format_display_name(str(self.name))
        if formatted_name:
            return formatted_name

        verbose_name = self.verbose_name
        formatted_verbose = self.format_display_name(str(verbose_name))
        return formatted_verbose or self.name

    class Meta:
        db_table = "pages_application"
        verbose_name = _("Application")
        verbose_name_plural = _("Applications")

    @classmethod
    def order_map(cls) -> dict[str, int]:
        return {
            name: order
            for name, order in cls.objects.filter(order__isnull=False).values_list(
                "name", "order"
            )
        }

    @staticmethod
    def format_display_name(name: str) -> str:
        cleaned_name = re.sub(r"^\s*\d+\.\s*", "", name or "").strip()
        return cleaned_name or str(name or "")
