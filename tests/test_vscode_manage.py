import os
import runpy
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
import vscode_manage


def test_wrapper_strips_debugpy(monkeypatch):
    monkeypatch.setenv("DEBUGPY_LAUNCHER_PORT", "1234")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/a", "/debugpy", "/b"]))

    called = {}
    monkeypatch.setattr(runpy, "run_path", lambda path, run_name: called.setdefault("path", path))

    vscode_manage.main(["runserver"])

    assert called["path"] == "manage.py"
    assert "DEBUGPY_LAUNCHER_PORT" not in os.environ
    assert "/debugpy" not in os.environ["PYTHONPATH"]
