from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand

from apps.app.models import Application
from apps.sites.defaults import DEFAULT_APPLICATION_DESCRIPTIONS


class Command(BaseCommand):
    help = "Create Application entries for installed local apps."

    def handle(self, *args, **options):
        Site.objects.filter(domain="zephyrus").delete()
        site, _ = Site.objects.update_or_create(
            domain="127.0.0.1", defaults={"name": "Local"}
        )
        application_apps = getattr(settings, "LOCAL_APPS", [])

        for app_label in application_apps:
            try:
                config = django_apps.get_app_config(app_label)
            except LookupError:
                config = next(
                    (c for c in django_apps.get_app_configs() if c.name == app_label),
                    None,
                )
                if config is None:
                    continue
            description = DEFAULT_APPLICATION_DESCRIPTIONS.get(config.label, "")
            app, created = Application.objects.get_or_create(
                name=config.label, defaults={"description": description}
            )
            updates = {}
            if description and app.description != description:
                updates["description"] = description
            if updates:
                app.__class__.objects.filter(pk=app.pk).update(**updates)
