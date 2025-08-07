import sys
import types
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils import git_sync


def test_restart_server_plain(monkeypatch):
    called = {}
    monkeypatch.setattr(git_sync.os, "execv", lambda exe, args: called.update(exe=exe, args=args))
    monkeypatch.setattr(git_sync.sys, "executable", "/usr/bin/python")
    monkeypatch.setattr(git_sync.sys, "argv", ["manage.py", "runserver"])
    monkeypatch.delitem(git_sync.sys.modules, "debugpy", raising=False)

    git_sync._restart_server()

    assert called["args"] == ["/usr/bin/python", "manage.py", "runserver"]


def test_restart_server_strips_debugpy_launcher_with_module(monkeypatch):
    called = {}
    monkeypatch.setattr(git_sync.os, "execv", lambda exe, args: called.update(exe=exe, args=args))
    monkeypatch.setattr(git_sync.sys, "executable", "/usr/bin/python")
    monkeypatch.setattr(
        git_sync.sys,
        "argv",
        ["debugpy_launcher", "5678", "--", "manage.py", "runserver", "--noreload"],
    )
    monkeypatch.setitem(git_sync.sys.modules, "debugpy", types.ModuleType("debugpy"))

    git_sync._restart_server()

    assert called["args"] == ["/usr/bin/python", "manage.py", "runserver", "--noreload"]


def test_restart_server_strips_debugpy_launcher_without_module(monkeypatch):
    called = {}
    monkeypatch.setattr(git_sync.os, "execv", lambda exe, args: called.update(exe=exe, args=args))
    monkeypatch.setattr(git_sync.sys, "executable", "/usr/bin/python")
    monkeypatch.setattr(
        git_sync.sys,
        "argv",
        ["debugpy_launcher", "5678", "--", "manage.py", "runserver"],
    )
    monkeypatch.delitem(git_sync.sys.modules, "debugpy", raising=False)

    git_sync._restart_server()

    assert called["args"] == ["/usr/bin/python", "manage.py", "runserver"]
