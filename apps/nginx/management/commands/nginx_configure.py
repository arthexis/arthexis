from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.nginx.management.commands.nginx import ConfigureMixin


class Command(ConfigureMixin, BaseCommand):
    """Supported management command alias forwarding to ``nginx --configure``.

    The alias is retained as a synonym for operators who still prefer the
    dedicated entrypoint, while delegating execution to the consolidated nginx
    CLI.
    """

    help = "Alias for `nginx --configure`."  # noqa: A003 - django requires 'help'

    def add_arguments(self, parser):
        """Accept alias flags and forward them to ``nginx --configure``."""

        self.add_configure_arguments(parser)

    def handle(self, *args, **options):
        """Forward the alias command to the consolidated nginx CLI."""

        self.stdout.write("`nginx_configure` is a supported alias for `nginx --configure`.")
        call_command(
            "nginx",
            "--configure",
            *self._forwarded_args(options),
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def _forwarded_args(self, options: dict[str, object]) -> list[str]:
        """Convert parsed alias options back into CLI flags for forwarding.

        Parameters:
            options: Parsed option values for ``mode``, ``port``, ``role``,
                ``ip6``, ``remove``, ``no_reload``, ``sites_config``, and
                ``sites_destination``.

        Returns:
            A list of CLI flag strings built from truthy option values.
        """

        forwarded: list[str] = []
        argument_specs = (
            ("mode", True),
            ("port", True),
            ("role", True),
            ("ip6", False),
            ("remove", False),
            ("no_reload", False),
            ("static_ip", True),
            ("sites_config", True),
            ("sites_destination", True),
        )
        for option_name, has_value in argument_specs:
            value = options[option_name]
            if not value:
                continue
            flag = f"--{option_name.replace('_', '-')}"
            if has_value:
                forwarded.extend([flag, str(value)])
            else:
                forwarded.append(flag)
        return forwarded
