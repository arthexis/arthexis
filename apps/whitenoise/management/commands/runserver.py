"""Runserver command wrapper that reuses WhiteNoise's ``nostatic`` behavior."""

from __future__ import annotations

from importlib import import_module

from django.apps import apps


def get_next_runserver_command():
    """Return the next highest-priority ``runserver`` command class."""

    for app_name in get_lower_priority_apps():
        module_path = f"{app_name}.management.commands.runserver"
        try:
            return import_module(module_path).Command
        except (ImportError, AttributeError):
            pass


def get_lower_priority_apps():
    """Yield app module names beneath ``apps.whitenoise`` in ``INSTALLED_APPS``."""

    reached_self = False
    for app_config in apps.get_app_configs():
        if app_config.name == "apps.whitenoise":
            reached_self = True
        elif reached_self:
            yield app_config.name
    yield "django.core"


RunserverCommand = get_next_runserver_command()


class Command(RunserverCommand):
    """Delegate to WhiteNoise's ``runserver_nostatic`` behavior."""


    def add_arguments(self, parser):
        super().add_arguments(parser)
        if parser.get_default("use_static_handler") is True:
            parser.set_defaults(use_static_handler=False)
            parser.description = (parser.description or "") + (
                "\n(Wrapped by 'apps.whitenoise' to always enable '--nostatic')"
            )
