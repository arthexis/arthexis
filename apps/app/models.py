from __future__ import annotations

import importlib
import re
from typing import Iterable

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connections, models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from utils.enabled_apps_lock import write_enabled_apps_lock

DEFAULT_MODEL_WIKI_URLS: dict[tuple[str, str], str] = {
    ("app", "app.Application"): "https://en.wikipedia.org/wiki/Application_software",
    ("ocpp", "ocpp.Charger"): "https://en.wikipedia.org/wiki/Open_Charge_Point_Protocol",
}


class ApplicationManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Application(Entity):
    class Importance(models.TextChoices):
        CRITICAL = "critical", _("Critical")
        BASELINE = "baseline", _("Baseline")
        PROTOTYPE = "prototype", _("Prototype")

    name = models.CharField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(blank=True, null=True)
    importance = models.CharField(
        max_length=20,
        choices=Importance.choices,
        default=Importance.BASELINE,
    )
    enabled = models.BooleanField(default=True)

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
        if not cleaned_name:
            return str(name or "")

        normalized = cleaned_name.lower()
        acronyms = {
            "ocpp": "OCPP",
        }
        return acronyms.get(normalized, cleaned_name)


class ApplicationModel(models.Model):
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="models",
    )
    label = models.CharField(max_length=255)
    model_name = models.CharField(max_length=100)
    verbose_name = models.CharField(max_length=255, blank=True)
    wiki_url = models.URLField(blank=True)

    class Meta:
        db_table = "pages_applicationmodel"
        verbose_name = _("Application model")
        verbose_name_plural = _("Application models")
        unique_together = ("application", "label")
        ordering = ("label",)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.label


def _get_models_for_application(app_config) -> Iterable[type[models.Model]]:
    return app_config.get_models() if app_config else []


def _refresh_application_models(
    using: str, applications: Iterable[Application] | None = None
) -> None:
    connection = connections[using]
    existing_tables = set(connection.introspection.table_names())
    required_tables = {
        Application._meta.db_table,
        ApplicationModel._meta.db_table,
    }

    if not required_tables.issubset(existing_tables):
        return

    application_qs = (
        Application.objects.using(using).filter(
            pk__in=[app.pk for app in applications if app.pk]
        )
        if applications is not None
        else Application.objects.using(using).all()
    )

    for application in application_qs:
        existing_wiki_urls = {
            model.label: model.wiki_url
            for model in ApplicationModel.objects.using(using)
            .filter(application=application)
            .only("label", "wiki_url")
        }

        try:
            app_config = django_apps.get_app_config(application.name)
        except LookupError:
            app_config = None

        application_models = [
            ApplicationModel(
                application=application,
                label=model._meta.label,
                model_name=model._meta.model_name,
                verbose_name=str(model._meta.verbose_name),
                wiki_url=(
                    existing_wiki_urls.get(model._meta.label, "")
                    or DEFAULT_MODEL_WIKI_URLS.get(
                        (application.name, model._meta.label), ""
                    )
                ),
            )
            for model in _get_models_for_application(app_config)
        ]

        with transaction.atomic(using=using):
            ApplicationModel.objects.using(using).filter(
                application=application
            ).delete()
            ApplicationModel.objects.using(using).bulk_create(application_models)


def refresh_application_models(
    using: str | None = None,
    applications: Iterable[Application] | None = None,
    **kwargs,
) -> None:
    database = using or kwargs.get("using") or "default"
    _refresh_application_models(database, applications=applications)


def _load_manifest_app_entries() -> set[str]:
    """Return normalized DJANGO_APPS entries discovered from app manifests."""

    try:
        from config.settings.base import APPS_DIR, BASE_DIR
    except Exception:  # pragma: no cover - startup fallback
        return set()

    app_entries: set[str] = set()
    for manifest_path in sorted(APPS_DIR.rglob("manifest.py")):
        module_name = ".".join(manifest_path.relative_to(BASE_DIR).with_suffix("").parts)
        try:
            module = importlib.import_module(module_name)
        except Exception:  # pragma: no cover - invalid manifests are validated elsewhere
            continue

        manifest_entries = getattr(module, "DJANGO_APPS", None)
        if not isinstance(manifest_entries, list):
            continue

        app_entries.update(
            entry.strip()
            for entry in manifest_entries
            if isinstance(entry, str) and entry.strip()
        )

    return app_entries


def refresh_enabled_apps_lock(using: str = "default"):
    """Persist enabled application selectors to the lock file for next restart."""

    connection = connections[using]
    try:
        existing_tables = set(connection.introspection.table_names())
    except (OperationalError, ProgrammingError):
        return None

    if Application._meta.db_table not in existing_tables:
        return None

    enabled_names = {
        name.strip()
        for name in Application.objects.using(using)
        .filter(enabled=True)
        .values_list("name", flat=True)
        if name and name.strip()
    }
    disabled_names = {
        name.strip()
        for name in Application.objects.using(using)
        .filter(enabled=False)
        .values_list("name", flat=True)
        if name and name.strip()
    }

    manifest_entries = _load_manifest_app_entries()
    manifest_enabled = {
        entry
        for entry in manifest_entries
        if entry not in disabled_names and entry.rsplit(".", 1)[-1] not in disabled_names
    }

    selectors = enabled_names | manifest_enabled
    return write_enabled_apps_lock(selectors, settings.BASE_DIR)
