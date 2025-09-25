from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.utils.text import slugify
import socket

from pages.models import Application, Module
from pages.defaults import DEFAULT_APPLICATION_DESCRIPTIONS
from nodes.models import Node, NodeRole


class Command(BaseCommand):
    help = (
        "Create Application entries for installed local apps and attach them to"
        " the Terminal node role."
    )

    def handle(self, *args, **options):
        Site.objects.filter(domain="zephyrus").delete()
        site, _ = Site.objects.update_or_create(
            domain="127.0.0.1", defaults={"name": "Local"}
        )
        role, _ = NodeRole.objects.get_or_create(name="Terminal")

        hostname = socket.gethostname()
        Node.objects.get_or_create(
            hostname=hostname,
            defaults={
                "address": "127.0.0.1",
                "port": 8000,
                "enable_public_api": False,
                "role": role,
            },
        )

        for app_label in getattr(settings, "LOCAL_APPS", []):
            try:
                config = django_apps.get_app_config(app_label)
            except LookupError:
                continue
            description = DEFAULT_APPLICATION_DESCRIPTIONS.get(config.label, "")
            app, created = Application.objects.get_or_create(
                name=config.label, defaults={"description": description}
            )
            if not created and description and app.description != description:
                app.description = description
                app.save(update_fields=["description"])
            path = f"/{slugify(app.name)}/"
            module, created = Module.objects.update_or_create(
                node_role=role, path=path, defaults={"application": app}
            )
            if created:
                module.create_landings()
