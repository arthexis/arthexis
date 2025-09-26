import importlib
import os
import sys
import types

import manage


class _FakeRunserverCommand:
    addr = None
    default_addr_ipv6 = "::1"
    use_ipv6 = False
    default_addr = "127.0.0.1"

    def __init__(self, *args, **kwargs):
        self.stdout = types.SimpleNamespace(write=lambda *a, **k: None)

    def on_bind(self, server_port):
        return None


def _install_fake_management(monkeypatch):
    fake_django = types.ModuleType("django")
    fake_core = types.ModuleType("django.core")
    fake_management = types.ModuleType("django.core.management")
    fake_management.execute_from_command_line = lambda argv: None

    fake_commands_pkg = types.ModuleType("django.core.management.commands")
    fake_core_runserver = types.ModuleType(
        "django.core.management.commands.runserver"
    )
    fake_core_runserver.Command = _FakeRunserverCommand
    setattr(fake_commands_pkg, "runserver", fake_core_runserver)

    fake_contrib = types.ModuleType("django.contrib")
    fake_staticfiles = types.ModuleType("django.contrib.staticfiles")
    fake_static_mgmt = types.ModuleType("django.contrib.staticfiles.management")
    fake_static_commands = types.ModuleType(
        "django.contrib.staticfiles.management.commands"
    )
    fake_static_runserver = types.ModuleType(
        "django.contrib.staticfiles.management.commands.runserver"
    )
    fake_static_runserver.Command = _FakeRunserverCommand
    setattr(fake_static_commands, "runserver", fake_static_runserver)

    fake_daphne = types.ModuleType("daphne")
    fake_daphne_management = types.ModuleType("daphne.management")
    fake_daphne_commands = types.ModuleType("daphne.management.commands")
    fake_daphne_runserver = types.ModuleType("daphne.management.commands.runserver")
    fake_daphne_runserver.Command = _FakeRunserverCommand

    modules = {
        "django": fake_django,
        "django.core": fake_core,
        "django.core.management": fake_management,
        "django.core.management.commands": fake_commands_pkg,
        "django.core.management.commands.runserver": fake_core_runserver,
        "django.contrib": fake_contrib,
        "django.contrib.staticfiles": fake_staticfiles,
        "django.contrib.staticfiles.management": fake_static_mgmt,
        "django.contrib.staticfiles.management.commands": fake_static_commands,
        "django.contrib.staticfiles.management.commands.runserver": fake_static_runserver,
        "daphne": fake_daphne,
        "daphne.management": fake_daphne_management,
        "daphne.management.commands": fake_daphne_commands,
        "daphne.management.commands.runserver": fake_daphne_runserver,
    }

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def _reload_manage():
    return importlib.reload(manage)


def _prepare_manage(monkeypatch):
    _install_fake_management(monkeypatch)
    module = _reload_manage()
    monkeypatch.setattr(module, "loadenv", lambda: None)
    monkeypatch.setattr(module.revision, "get_revision", lambda: "")
    return module


def test_runserver_never_sets_debug(monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    module = _prepare_manage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver"])

    module.main()

    assert "DEBUG" not in os.environ


def test_runserver_preserves_existing_debug(monkeypatch):
    monkeypatch.setenv("DEBUG", "1")
    module = _prepare_manage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver"])

    module.main()

    assert os.environ["DEBUG"] == "1"
