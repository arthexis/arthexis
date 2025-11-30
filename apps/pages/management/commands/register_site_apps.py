from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.utils.text import slugify
import socket

from apps.app.models import Application
from apps.pages.models import Module
from apps.pages.defaults import DEFAULT_APPLICATION_DESCRIPTIONS
from apps.nodes.models import Node, NodeRole


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
        node_defaults = {
            "address": "127.0.0.1",
            "port": 8888,
            "role": role,
        }

        existing_nodes = Node.objects.filter(hostname=hostname).order_by("pk")
        existing_node = existing_nodes.first()
        if existing_node:
            updates = {
                field: value
                for field, value in node_defaults.items()
                if getattr(existing_node, field) != value
            }
            if updates:
                Node.objects.filter(pk=existing_node.pk).update(**updates)
        else:
            Node.objects.create(hostname=hostname, **node_defaults)

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
            path = f"/{slugify(app.name)}/"
            module, created = Module.objects.update_or_create(
                node_role=role, path=path, defaults={"application": app}
            )
            if created:
                module.create_landings()
