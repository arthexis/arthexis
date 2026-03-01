from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class DatabaseConnectionInfo:
    """Serializable subset of Django database configuration values."""

    alias: str
    engine: str
    name: str
    host: str
    port: str
    user: str


class ManagedDatabase(models.Model):
    """Database connection represented as a manageable Django model."""

    alias = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=120, blank=True)
    engine = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True)
    host = models.CharField(max_length=255, blank=True)
    port = models.CharField(max_length=32, blank=True)
    username = models.CharField(max_length=255, blank=True)
    is_current = models.BooleanField(default=False)
    is_django_connection = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_current", "alias"]
        verbose_name = _("Managed Database")
        verbose_name_plural = _("Managed Databases")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display_name or self.alias

    @classmethod
    def sync_from_settings(cls) -> list["ManagedDatabase"]:
        """Synchronize rows from Django settings and mark the current connection."""

        configured_aliases = set(settings.DATABASES.keys())
        synced_records: list[ManagedDatabase] = []
        for alias in sorted(configured_aliases):
            connection_info = cls._build_connection_info(alias=alias)
            defaults = {
                "display_name": cls._build_display_name(connection_info),
                "engine": connection_info.engine,
                "name": connection_info.name,
                "host": connection_info.host,
                "port": connection_info.port,
                "username": connection_info.user,
                "is_current": alias == "default",
                "is_django_connection": True,
            }
            obj, _ = cls.objects.update_or_create(alias=alias, defaults=defaults)
            synced_records.append(obj)

        cls.objects.exclude(alias__in=configured_aliases).update(is_current=False)
        return synced_records

    @staticmethod
    def _build_display_name(connection_info: DatabaseConnectionInfo) -> str:
        """Build a human-readable label for a configured connection."""

        return f"{connection_info.alias}: {connection_info.name or 'unnamed database'}"

    @staticmethod
    def _build_connection_info(*, alias: str) -> DatabaseConnectionInfo:
        """Extract connection information for an alias from Django settings."""

        raw_config = settings.DATABASES.get(alias, {})
        return DatabaseConnectionInfo(
            alias=alias,
            engine=str(raw_config.get("ENGINE", "")),
            name=str(raw_config.get("NAME", "")),
            host=str(raw_config.get("HOST", "")),
            port=str(raw_config.get("PORT", "")),
            user=str(raw_config.get("USER", "")),
        )
