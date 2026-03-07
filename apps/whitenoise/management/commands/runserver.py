"""Runserver command wrapper that reuses WhiteNoise's ``nostatic`` behavior."""

from whitenoise.runserver_nostatic.management.commands.runserver import (
    Command as WhiteNoiseNoStaticCommand,
)


class Command(WhiteNoiseNoStaticCommand):
    """Delegate to WhiteNoise's runserver implementation without static overrides."""
