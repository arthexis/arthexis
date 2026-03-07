"""Tests for local WhiteNoise runserver command registration."""

from django.core.management import CommandParser, get_commands, load_command_class
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticFilesRunserverCommand,
)
from django.test import SimpleTestCase


class WhiteNoiseRunserverCommandTests(SimpleTestCase):
    """Validate the local no-underscore WhiteNoise runserver command alias."""

    def test_runserver_command_is_registered_to_local_whitenoise_app(self) -> None:
        """Ensure runserver resolves to the local WhiteNoise app module."""

        command_map = get_commands()

        assert command_map["runserver"] == "apps.whitenoise"

    def test_local_whitenoise_runserver_command_wraps_staticfiles_command(self) -> None:
        """Ensure local wrapper inherits from staticfiles runserver command."""

        command_app = get_commands()["runserver"]
        command = load_command_class(command_app, "runserver")

        assert issubclass(command.__class__, StaticFilesRunserverCommand)
        assert command.__class__ is not StaticFilesRunserverCommand

    def test_local_whitenoise_runserver_exposes_staticfiles_options(self) -> None:
        """Ensure inherited staticfiles options (for ``--nostatic``) are preserved."""

        command_app = get_commands()["runserver"]
        command = load_command_class(command_app, "runserver")
        parser = CommandParser(prog="manage.py runserver", missing_args_message="")

        command.add_arguments(parser)

        assert parser.get_default("use_static_handler") is False
        options = {
            option
            for action in parser._actions
            for option in action.option_strings
            if option
        }
        assert "--nostatic" in options
        assert "--insecure" in options
