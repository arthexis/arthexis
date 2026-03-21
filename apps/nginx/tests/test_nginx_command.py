from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nginx import services


class _DummyConfig:
    """Simple stand-in for SiteConfiguration used by nginx command tests."""

    def __init__(self):
        self.mode = "internal"
        self.port = 8888
        self.role = "Terminal"
        self.include_ipv6 = False
        self.site_entries_path = "scripts/generated/nginx-sites.json"
        self.site_destination = "/etc/nginx/sites-enabled/arthexis-sites.conf"
        self.enabled = True
        self.saved = False
        self.apply_calls: list[dict[str, object]] = []

    def save(self):
        """Record that the configuration was saved during a test run."""
        self.saved = True

    def apply(self, *, reload: bool = True, remove: bool = False):
        """Record apply arguments and return a deterministic nginx apply result."""
        self.apply_calls.append({"reload": reload, "remove": remove})
        return services.ApplyResult(
            changed=True,
            validated=False,
            reloaded=False,
            message="Applied nginx configuration.",
        )


def test_nginx_configure_requires_an_explicit_action():
    """The consolidated nginx command should require a top-level action flag."""

    with pytest.raises(CommandError, match="Use --configure"):
        call_command("nginx")


def test_nginx_configure_applies_configuration_with_tighter_messages(monkeypatch):
    """`nginx --configure` should reuse the configure flow and print tighter status output."""

    dummy = _DummyConfig()

    from apps.nginx.management.commands import nginx as nginx_command

    monkeypatch.setattr(
        nginx_command.SiteConfiguration,
        "get_default",
        staticmethod(lambda: dummy),
    )

    stdout = StringIO()
    call_command("nginx", "--configure", "--no-reload", stdout=stdout)

    output = stdout.getvalue()
    assert dummy.saved is True
    assert dummy.apply_calls == [{"reload": False, "remove": False}]
    assert "Applied nginx configuration." in output
    assert (
        "nginx applied the configuration, but validation was skipped or failed."
        in output
    )
    assert "nginx was not reloaded automatically; check the service status." in output


def test_https_remediation_message_points_to_nginx_configure():
    """HTTPS remediation guidance should point operators to the consolidated nginx command."""

    from apps.nginx.management.commands.https_parts.constants import (
        NGINX_CONFIGURE_REMEDIATION_TEMPLATE,
    )

    message = NGINX_CONFIGURE_REMEDIATION_TEMPLATE.format(command="./manage.py")
    assert "./manage.py nginx --configure" in message
    assert "rerun the HTTPS command" in message


def test_legacy_nginx_configure_forwards_to_consolidated_command(monkeypatch):
    """The legacy alias should forward arguments to `nginx --configure`."""

    forwarded = {}

    def fake_call_command(name, *args, **kwargs):
        forwarded["name"] = name
        forwarded["args"] = args
        forwarded["kwargs"] = kwargs

    from apps.nginx.management.commands import (
        nginx_configure as nginx_configure_command,
    )

    monkeypatch.setattr(nginx_configure_command, "call_command", fake_call_command)

    stdout = StringIO()
    call_command(
        "nginx_configure",
        "--mode",
        "public",
        "--port",
        "9443",
        "--no-reload",
        stdout=stdout,
    )

    assert "deprecated" in stdout.getvalue().lower()
    assert forwarded["name"] == "nginx"
    assert forwarded["args"] == (
        "--configure",
        "--mode",
        "public",
        "--port",
        "9443",
        "--no-reload",
    )
