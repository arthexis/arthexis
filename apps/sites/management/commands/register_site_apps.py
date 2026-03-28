from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand

from apps.app.models import Application
from apps.sites.defaults import DEFAULT_APPLICATION_DESCRIPTIONS


class Command(BaseCommand):
    help = "Create Application entries for installed local apps."

    @staticmethod
    def _application_labels() -> list[str]:
        """Return local application labels that should exist in ``Application``."""

        configured_apps = getattr(settings, "PROJECT_LOCAL_APPS", None)
        if not isinstance(configured_apps, list):
            configured_apps = getattr(settings, "LOCAL_APPS", [])

        labels: set[str] = set()
        for app_entry in configured_apps:
            if not isinstance(app_entry, str):
                continue
            app_path = app_entry.strip()
            if not app_path:
                continue

            try:
                config = django_apps.get_app_config(app_path)
            except LookupError:
                config = next(
                    (candidate for candidate in django_apps.get_app_configs() if candidate.name == app_path),
                    None,
                )
            if config is not None:
                labels.add(config.label)

        if labels:
            return sorted(labels)

        return sorted(
            config.label for config in django_apps.get_app_configs() if config.name.startswith("apps.")
        )

    def handle(self, *args, **options):
        Site.objects.filter(domain="zephyrus").delete()
        site, _ = Site.objects.update_or_create(
            domain="127.0.0.1", defaults={"name": "Local"}
        )
        del site

        for app_label in self._application_labels():
            config = django_apps.get_app_config(app_label)
            description = DEFAULT_APPLICATION_DESCRIPTIONS.get(config.label, "")
            app, created = Application.objects.get_or_create(
                name=config.label, defaults={"description": description}
            )
            updates = {}
            if description and app.description != description:
                updates["description"] = description
            if updates:
                app.__class__.objects.filter(pk=app.pk).update(**updates)
