import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils import git_sync


def test_restart_server_plain(monkeypatch):
    called = {}
    monkeypatch.setattr(git_sync.os, "execv", lambda exe, args: called.update(exe=exe, args=args))
    monkeypatch.setattr(git_sync.sys, "executable", "/usr/bin/python")
    monkeypatch.setattr(git_sync.sys, "argv", ["manage.py", "runserver"])

    git_sync._restart_server()

    assert called["args"] == ["/usr/bin/python", "manage.py", "runserver"]
