from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand

from website.models import Application, SiteApplication


class Command(BaseCommand):
    help = (
        "Create Application entries for installed local apps and attach them to"
        " the default localhost site."
    )

    def handle(self, *args, **options):
        site, _ = Site.objects.get_or_create(
            domain="127.0.0.1", defaults={"name": "localhost"}
        )

        for app_label in getattr(settings, "LOCAL_APPS", []):
            try:
                config = django_apps.get_app_config(app_label)
            except LookupError:
                continue
            app, _ = Application.objects.get_or_create(name=config.label)
            if not SiteApplication.objects.filter(site=site, application=app).exists():
                SiteApplication.objects.create(site=site, application=app)
