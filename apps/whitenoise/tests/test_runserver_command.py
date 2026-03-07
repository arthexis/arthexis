"""Tests for local WhiteNoise runserver command registration."""

from django.core.management import get_commands, load_command_class
from django.test import SimpleTestCase


class WhiteNoiseRunserverCommandTests(SimpleTestCase):
    """Validate the local no-underscore WhiteNoise runserver command alias."""

    def test_runserver_command_is_registered_to_local_whitenoise_app(self) -> None:
        """Ensure runserver resolves to the local WhiteNoise app module."""

        command_map = get_commands()

        assert command_map["runserver"] == "apps.whitenoise"

    def test_local_whitenoise_runserver_command_delegates_to_whitenoise(self) -> None:
        """Ensure local wrapper module is loadable as the active runserver command."""

        command_app = get_commands()["runserver"]
        command = load_command_class(command_app, "runserver")

        assert command.__module__.startswith("apps.whitenoise.management.commands")
