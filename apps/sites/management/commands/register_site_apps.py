from collections.abc import Sequence

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand

from apps.app.models import Application
from apps.sites.defaults import DEFAULT_APPLICATION_DESCRIPTIONS
from utils.enabled_apps_lock import read_enabled_apps_lock


class Command(BaseCommand):
    help = "Create Application entries for installed local apps."

    @staticmethod
    def _selector_aliases(selector: str) -> set[str]:
        normalized = selector.strip()
        if not normalized:
            return set()

        aliases = {normalized, normalized.rsplit(".", maxsplit=1)[-1]}
        if normalized.startswith("apps."):
            aliases.add(normalized.removeprefix("apps."))
        elif "." not in normalized:
            aliases.add(f"apps.{normalized}")
        return aliases

    @classmethod
    def _labels_for_app_entries(
        cls,
        configured_apps: Sequence[str],
        *,
        include_uninstalled: bool = False,
    ) -> set[str]:
        labels: set[str] = set()
        app_configs = tuple(django_apps.get_app_configs())
        for app_entry in configured_apps:
            if not isinstance(app_entry, str):
                continue
            app_path = app_entry.strip()
            if not app_path:
                continue

            config = next(
                (candidate for candidate in app_configs if candidate.name == app_path),
                None,
            )
            if config is None:
                try:
                    config = django_apps.get_app_config(app_path)
                except LookupError:
                    if include_uninstalled:
                        label = app_path.rsplit(".", maxsplit=1)[-1]
                        labels.add(label)
                    continue
            labels.add(config.label)
        return labels

    @classmethod
    def _project_app_entries(cls) -> Sequence[str]:
        configured_apps = getattr(settings, "PROJECT_LOCAL_APPS", None)
        if not isinstance(configured_apps, Sequence) or isinstance(
            configured_apps, (str, bytes)
        ):
            return getattr(settings, "LOCAL_APPS", [])
        return configured_apps

    @classmethod
    def _optional_app_entries(cls) -> Sequence[str]:
        optional_apps = getattr(settings, "OPTIONAL_PROJECT_LOCAL_APPS", [])
        if not isinstance(optional_apps, Sequence) or isinstance(
            optional_apps, (str, bytes)
        ):
            return []
        return optional_apps

    @classmethod
    def _application_labels(cls) -> list[str]:
        """Return local application labels that should exist in ``Application``."""

        labels = cls._labels_for_app_entries(cls._project_app_entries())
        labels.update(
            cls._labels_for_app_entries(
                cls._optional_app_entries(),
                include_uninstalled=True,
            )
        )

        if labels:
            return sorted(labels)

        return sorted(
            config.label
            for config in django_apps.get_app_configs()
            if config.name.startswith("apps.")
        )

    @classmethod
    def _optional_application_labels(cls) -> set[str]:
        return cls._labels_for_app_entries(
            cls._optional_app_entries(),
            include_uninstalled=True,
        )

    @classmethod
    def _enabled_lock_aliases(cls) -> set[str]:
        lock_entries = read_enabled_apps_lock(settings.BASE_DIR) or set()
        return {
            alias
            for selector in lock_entries
            for alias in cls._selector_aliases(selector)
        }

    def handle(self, *args, **options):
        Site.objects.filter(domain="zephyrus").delete()
        _, _ = Site.objects.update_or_create(
            domain="127.0.0.1", defaults={"name": "Local"}
        )

        optional_labels = self._optional_application_labels()
        enabled_lock_aliases = self._enabled_lock_aliases()
        for app_label in self._application_labels():
            try:
                config = django_apps.get_app_config(app_label)
                label = config.label
            except LookupError:
                label = app_label
            description = DEFAULT_APPLICATION_DESCRIPTIONS.get(label, "")
            defaults = {"description": description}
            if app_label in optional_labels:
                defaults["enabled"] = bool(
                    self._selector_aliases(app_label) & enabled_lock_aliases
                )
            app, _ = Application.objects.get_or_create(
                name=label,
                defaults=defaults,
            )
            updates = {}
            if description and app.description != description:
                updates["description"] = description
            if updates:
                app.__class__.objects.filter(pk=app.pk).update(**updates)
