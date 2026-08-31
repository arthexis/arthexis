from __future__ import annotations

from types import SimpleNamespace

from django.core.management.base import CommandError

from apps.nginx.management.commands.nginx import ConfigureMixin


class _ConfigureHarness(ConfigureMixin):
    def __init__(self):
        self.stdout = SimpleNamespace(write=lambda _message: None)
        self.style = SimpleNamespace(SUCCESS=lambda message: message)


def test_run_configure_skips_ip_checks_for_remove(monkeypatch):
    command = _ConfigureHarness()
    monkeypatch.setattr(command, "_detect_public_ips", lambda: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(command, "_parse_static_ip", lambda _: (_ for _ in ()).throw(AssertionError("unexpected")))

    class _Config:
        mode = "internal"
        port = 8888
        role = "Terminal"
        include_ipv6 = False
        site_entries_path = "scripts/generated/nginx-sites.json"
        site_destination = "/etc/nginx/sites-enabled/arthexis-sites.conf"

        def save(self):
            return None

        def apply(self, *, reload, remove):
            assert reload is True
            assert remove is True
            return SimpleNamespace(message="removed", validated=True, reloaded=True)

    monkeypatch.setattr(
        "apps.nginx.management.commands.nginx.SiteConfiguration.get_default",
        lambda: _Config(),
    )

    command.run_configure(
        {
            "mode": None,
            "port": None,
            "role": None,
            "ip6": False,
            "sites_config": None,
            "sites_destination": None,
            "remove": True,
            "no_reload": False,
            "static_ip": None,
        }
    )
