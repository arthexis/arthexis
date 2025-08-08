import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
import manage_vscode


def test_wrapper_execs_manage(monkeypatch):
    captured = {}

    def fake_execv(exe, args):
        captured["exe"] = exe
        captured["args"] = args
        captured["env"] = dict(os.environ)

    monkeypatch.setattr(manage_vscode.os, "execv", fake_execv)
    monkeypatch.setattr(manage_vscode.sys, "executable", "/usr/bin/python")
    monkeypatch.setattr(manage_vscode.sys, "argv", ["manage_vscode.py", "runserver"])
    monkeypatch.setenv("DEBUGPY_LAUNCHER_PORT", "5678")
    monkeypatch.setenv("PYTHONPATH", f"/tmp/debugpy{os.pathsep}/usr/lib")

    manage_vscode.main()

    manage = Path(manage_vscode.__file__).resolve().parent / "manage.py"
    assert captured["args"] == ["/usr/bin/python", str(manage), "runserver"]
    assert "DEBUGPY_LAUNCHER_PORT" not in captured["env"]
    assert "debugpy" not in captured["env"].get("PYTHONPATH", "")
