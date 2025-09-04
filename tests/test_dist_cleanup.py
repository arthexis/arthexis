from pathlib import Path
import sys
import subprocess
import types

from core import release


def test_build_removes_dist_contents(monkeypatch, tmp_path):
    base = tmp_path
    (base / "requirements.txt").write_text("")
    monkeypatch.chdir(base)
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "build", types.SimpleNamespace())

    dist = base / "dist"
    (dist / ".tmp-old").mkdir(parents=True)
    (dist / "old.txt").write_text("old")

    def fake_run(cmd, check=True):
        if cmd[:3] == [sys.executable, "-m", "build"]:
            dist.mkdir(exist_ok=True)
            (dist / "new.txt").write_text("new")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(release, "_run", fake_run)

    release.build(version="1.2.3", dist=True)

    assert not (dist / ".tmp-old").exists()
    assert (dist / "new.txt").exists()
