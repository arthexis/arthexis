from __future__ import annotations

import pytest
from django.core.management.base import CommandError

from apps.nginx.management.commands.nginx import ConfigureMixin


class _ConfigureHarness(ConfigureMixin):
    def __init__(self):
        self.stdout = None
        self.style = None


def test_parse_static_ip_rejects_private_ip():
    command = _ConfigureHarness()
    with pytest.raises(CommandError, match="public-routable"):
        command._parse_static_ip("10.0.0.8")


def test_run_configure_requires_public_detection_or_static_ip(monkeypatch):
    command = _ConfigureHarness()
    monkeypatch.setattr(command, "_detect_public_ips", lambda: [])
    monkeypatch.setattr(command, "_parse_static_ip", lambda _: None)

    with pytest.raises(CommandError, match="No public/static IP was detected"):
        command.run_configure(
            {
                "mode": None,
                "port": None,
                "role": None,
                "ip6": False,
                "sites_config": None,
                "sites_destination": None,
                "remove": False,
                "no_reload": False,
                "static_ip": None,
            }
        )
