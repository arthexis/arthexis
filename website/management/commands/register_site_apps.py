from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.utils.text import slugify
import socket

from website.models import Application, Module
from nodes.models import Node, NodeRole


class Command(BaseCommand):
    help = (
        "Create Application entries for installed local apps and attach them to"
        " the Terminal node role."
    )

    def handle(self, *args, **options):
        site, _ = Site.objects.get_or_create(
            domain="127.0.0.1", defaults={"name": "local"}
        )
        role, _ = NodeRole.objects.get_or_create(name="Terminal")

        hostname = socket.gethostname()
        Node.objects.get_or_create(
            hostname=hostname,
            defaults={
                "address": "127.0.0.1",
                "port": 8000,
                "enable_public_api": False,
                "clipboard_polling": False,
                "screenshot_polling": False,
                "role": role,
            },
        )

        for app_label in getattr(settings, "LOCAL_APPS", []):
            try:
                config = django_apps.get_app_config(app_label)
            except LookupError:
                continue
            app, _ = Application.objects.get_or_create(name=config.label)
            path = f"/{slugify(app.name)}/"
            module, created = Module.objects.update_or_create(
                node_role=role, path=path, defaults={"application": app}
            )
            if created:
                module.create_landings()
