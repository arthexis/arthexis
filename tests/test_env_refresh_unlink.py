import ast
import re
from pathlib import Path
from types import SimpleNamespace


def load_unlink_function():
    source = Path(__file__).resolve().parents[1] / "env-refresh.py"
    module = ast.parse(source.read_text())
    func_node = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_unlink_sqlite_db"
    )
    module = ast.Module([func_node], [])  # type: ignore[arg-type]
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {"Path": Path, "re": re}
    code = compile(module, filename="env-refresh.py", mode="exec")
    exec(code, namespace)
    return namespace["_unlink_sqlite_db"]  # type: ignore[return-value]


def test_unlink_sqlite_db_retries(monkeypatch, tmp_path):
    unlink_func = load_unlink_function()
    path = tmp_path / "db.sqlite3"
    path.touch()

    calls = {"count": 0}
    original_unlink = Path.unlink

    def fake_unlink(self, missing_ok=False):
        if self == path and calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    monkeypatch.setitem(
        unlink_func.__globals__, "connections", SimpleNamespace(close_all=lambda: None)
    )
    monkeypatch.setitem(
        unlink_func.__globals__, "time", SimpleNamespace(sleep=lambda s: None)
    )
    monkeypatch.setitem(
        unlink_func.__globals__, "settings", SimpleNamespace(BASE_DIR=tmp_path)
    )
    monkeypatch.setitem(
        unlink_func.__globals__,
        "datetime",
        SimpleNamespace(now=lambda: SimpleNamespace(strftime=lambda fmt: "20240101")),
    )
    monkeypatch.setitem(
        unlink_func.__globals__, "shutil", SimpleNamespace(copy2=lambda *a, **k: None)
    )
    unlink_func(path)

    assert calls["count"] == 1
    assert not path.exists()
