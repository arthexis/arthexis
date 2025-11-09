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
    monkeypatch.setattr(
        runpy, "run_path", lambda path, run_name: called.setdefault("path", path)
    )

    vscode_manage.main(["runserver"])

    assert called["path"] == "manage.py"
    assert "DEBUGPY_LAUNCHER_PORT" not in os.environ
    assert "/debugpy" not in os.environ["PYTHONPATH"]
    assert os.environ["DEBUG"] == "1"
    assert sys.argv == ["manage.py", "runserver", "--noreload"]


def test_wrapper_does_not_set_debug_env_without_debugger(monkeypatch):
    monkeypatch.delenv("DEBUGPY_LAUNCHER_PORT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    called = {}
    monkeypatch.setattr(
        runpy, "run_path", lambda path, run_name: called.setdefault("path", path)
    )

    monkeypatch.setattr(sys, "argv", ["python"])

    vscode_manage.main(["runserver"])

    assert called["path"] == "manage.py"
    assert "DEBUG" not in os.environ
    assert sys.argv == ["manage.py", "runserver", "--noreload"]


def test_wrapper_adds_noreload_for_debug_sessions(monkeypatch):
    monkeypatch.setenv("DEBUGPY_LAUNCHER_PORT", "1234")

    monkeypatch.setattr(runpy, "run_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["python"])

    vscode_manage.main(["runserver", "0.0.0.0:8000"])

    assert sys.argv == ["manage.py", "runserver", "--noreload", "0.0.0.0:8000"]
