import types
import subprocess

from core import release


def test_promote_deletes_existing_branch(monkeypatch):
    calls = []
    first = True

    def fake_run(cmd, check=True):
        nonlocal first
        calls.append(cmd)
        if cmd[:3] == ["git", "checkout", "-b"] and first:
            first = False
            raise subprocess.CalledProcessError(1, cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "build", lambda **kwargs: None)
    monkeypatch.setattr(release, "_current_branch", lambda: "main")
    monkeypatch.setattr(release, "_current_commit", lambda: "abcdef123456")

    release.promote(version="1.0.0")

    assert calls[0] == ["git", "checkout", "-b", "release/1.0.0"]
    assert calls[1] == ["git", "branch", "-D", "release/1.0.0"]
    assert calls[2] == ["git", "checkout", "-b", "release/1.0.0"]
