from __future__ import annotations

import os
import re
from collections.abc import Iterable

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connections, models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from utils.app_manifests import (
    load_manifest_app_entries,
    load_manifest_declared_app_entries,
)
from utils.enabled_apps_lock import (
    get_enabled_apps_lock_path,
    read_enabled_apps_lock,
    read_enabled_apps_lock_direct_entries,
    read_enabled_apps_lock_direct_sources,
    write_enabled_apps_lock,
)
from utils.role_app_profiles import (
    FULL_SUITE_DIRECT_ROUTE_SELECTORS,
    PUBLIC_COMMERCE_DIRECT_ROUTE_SELECTORS,
    RETIRED_RUNTIME_APP_SELECTORS,
    explain_role_app_selectors,
    get_direct_lock_app_selectors,
    get_direct_lock_app_sources,
)

DEFAULT_MODEL_WIKI_URLS: dict[tuple[str, str], str] = {
    ("app", "app.Application"): "https://en.wikipedia.org/wiki/Application_software",
    (
        "ocpp",
        "ocpp.Charger",
    ): "https://en.wikipedia.org/wiki/Open_Charge_Point_Protocol",
}


class ApplicationManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Application(Entity):
    class Importance(models.TextChoices):
        CRITICAL = "critical", _("Critical")
        BASELINE = "baseline", _("Baseline")
        LEGACY = "legacy", _("Legacy")
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

    return load_manifest_app_entries(settings.BASE_DIR) - RETIRED_RUNTIME_APP_SELECTORS


def _load_manifest_declared_app_entries() -> set[str]:
    """Return normalized app entries discovered from runtime and optional manifests."""

    return (
        load_manifest_declared_app_entries(settings.BASE_DIR)
        - RETIRED_RUNTIME_APP_SELECTORS
    )


def _app_selector_aliases(selector: str) -> set[str]:
    normalized = selector.strip()
    if not normalized:
        return set()

    aliases = {normalized, normalized.rsplit(".", maxsplit=1)[-1]}
    if normalized.startswith("apps."):
        aliases.add(normalized.removeprefix("apps."))
    elif "." not in normalized:
        aliases.add(f"apps.{normalized}")
    return aliases


def _preserve_direct_app_selectors(
    direct_entries: Iterable[str] | None,
    selectors: Iterable[str],
    *,
    seed_direct_entries: Iterable[str] = (),
) -> set[str] | None:
    enabled_aliases = {
        alias for selector in selectors for alias in _app_selector_aliases(selector)
    }

    if direct_entries is None:
        if not seed_direct_entries:
            return None

        seeded_direct_apps = {
            selector
            for selector in seed_direct_entries
            if _app_selector_aliases(selector) & enabled_aliases
        }
        return seeded_direct_apps or None

    return {
        entry.strip()
        for entry in direct_entries
        if entry and _app_selector_aliases(entry) & enabled_aliases
    }


def _preserve_optional_app_selectors(
    lock_entries: Iterable[str] | None,
    optional_entries: Iterable[str],
    *,
    disabled_names: Iterable[str] = (),
) -> set[str]:
    """Return existing optional lock selectors that are not explicitly disabled."""

    if lock_entries is None:
        return set()

    optional_aliases = {
        alias for selector in optional_entries for alias in _app_selector_aliases(selector)
    }
    disabled_aliases = {
        alias for selector in disabled_names for alias in _app_selector_aliases(selector)
    }
    return {
        entry.strip()
        for entry in lock_entries
        if entry
        and _app_selector_aliases(entry) & optional_aliases
        and not (_app_selector_aliases(entry) & disabled_aliases)
    }


def _split_role_app_setting_entries(*names: str) -> tuple[str, ...]:
    entries: list[str] = []
    for name in names:
        raw_value = os.environ.get(name, "")
        entries.extend(part for part in re.split(r"[,;\s]+", raw_value) if part)
    return tuple(dict.fromkeys(entries))


def _charger_facing_route_locks_enabled() -> bool:
    lock_dir = settings.BASE_DIR / ".locks"
    return (lock_dir / "charger_facing.lck").exists() or (
        lock_dir / "ocpp_gateway.lck"
    ).exists()


def _with_charger_facing_direct_metadata(
    selectors: Iterable[str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    direct_selectors = tuple(dict.fromkeys(selectors))
    if not _charger_facing_route_locks_enabled():
        return direct_selectors, {}
    charger_selectors = ("apps.ocpp",)
    additions = tuple(
        selector for selector in charger_selectors if selector not in direct_selectors
    )
    if not additions:
        return direct_selectors, {}
    return tuple(dict.fromkeys((*direct_selectors, *additions))), {
        selector: "charger-facing" for selector in additions
    }


def _with_charger_facing_direct_selectors(selectors: Iterable[str]) -> tuple[str, ...]:
    direct_selectors, _sources = _with_charger_facing_direct_metadata(selectors)
    return direct_selectors


def _first_lock_direct_app_metadata() -> tuple[tuple[str, ...], dict[str, str]]:
    if not getattr(settings, "ROLE_APP_PROFILES_ENABLED", False):
        return _with_charger_facing_direct_metadata(FULL_SUITE_DIRECT_ROUTE_SELECTORS)

    try:
        result = explain_role_app_selectors(
            getattr(settings, "NODE_ROLE", "Terminal"),
            feature_packs=_split_role_app_setting_entries(
                "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
                "ARTHEXIS_FEATURE_PACKS",
            ),
            disabled_apps=_split_role_app_setting_entries(
                "ARTHEXIS_ROLE_APP_DISABLED_APPS",
                "ARTHEXIS_DISABLED_APPS",
            ),
        )
    except ValueError:
        return _with_charger_facing_direct_metadata(FULL_SUITE_DIRECT_ROUTE_SELECTORS)
    direct_selectors, route_sources = _with_charger_facing_direct_metadata(
        get_direct_lock_app_selectors(result)
    )
    return direct_selectors, {**get_direct_lock_app_sources(result), **route_sources}


def _first_lock_direct_app_selectors() -> tuple[str, ...]:
    direct_selectors, _sources = _first_lock_direct_app_metadata()
    return direct_selectors


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
    declared_manifest_entries = _load_manifest_declared_app_entries()
    optional_manifest_entries = declared_manifest_entries - manifest_entries
    manifest_enabled = {
        entry
        for entry in manifest_entries
        if entry not in disabled_names
        and entry.rsplit(".", 1)[-1] not in disabled_names
    }

    base_dir = settings.BASE_DIR
    enabled_apps_lock_exists = get_enabled_apps_lock_path(base_dir).exists()
    existing_lock_entries = read_enabled_apps_lock(base_dir)
    preserved_optional_apps = _preserve_optional_app_selectors(
        existing_lock_entries,
        optional_manifest_entries,
        disabled_names=disabled_names,
    )
    selectors = enabled_names | manifest_enabled | preserved_optional_apps
    existing_direct_entries = read_enabled_apps_lock_direct_entries(base_dir)
    direct_sources = read_enabled_apps_lock_direct_sources(base_dir)
    seed_direct_entries: tuple[str, ...] = ()
    seed_direct_sources: dict[str, str] = {}
    if not enabled_apps_lock_exists:
        seed_direct_entries, seed_direct_sources = _first_lock_direct_app_metadata()
    elif existing_direct_entries is None and _charger_facing_route_locks_enabled():
        seed_direct_entries, seed_direct_sources = _with_charger_facing_direct_metadata(
            ()
        )
    preserved_direct_apps = _preserve_direct_app_selectors(
        existing_direct_entries,
        selectors,
        seed_direct_entries=seed_direct_entries,
    )
    if existing_direct_entries is None:
        preserved_direct_sources = seed_direct_sources
    else:
        preserved_direct_sources = {
            selector: source
            for selector, source in direct_sources.items()
            if preserved_direct_apps and selector in preserved_direct_apps
        }
    return write_enabled_apps_lock(
        selectors,
        base_dir,
        direct_apps=preserved_direct_apps,
        direct_app_sources=preserved_direct_sources,
    )
