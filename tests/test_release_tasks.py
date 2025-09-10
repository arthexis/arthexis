import types

import pytest

import core.tasks as tasks


def _setup_tmp(monkeypatch, tmp_path):
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    fake_file = core_dir / "tasks.py"
    fake_file.write_text("")
    monkeypatch.setattr(tasks, "__file__", str(fake_file))
    return tmp_path


@pytest.mark.role("Constellation")
@pytest.mark.role("Virtual")
def test_no_upgrade_triggers_startup(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")

    def fake_run(*args, **kwargs):
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: b"1.0")

    called = {}
    import nodes.apps as nodes_apps
    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: called.setdefault("x", True))

    tasks.check_github_updates()

    assert called.get("x")


@pytest.mark.role("Constellation")
@pytest.mark.role("Virtual")
def test_upgrade_shows_message(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")

    run_calls = []

    def fake_run(args, cwd=None, check=None):
        run_calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: b"2.0")

    notify_calls = []
    import core.notifications as notifications
    monkeypatch.setattr(notifications, "notify", lambda subject, body="": notify_calls.append((subject, body)))

    tasks.check_github_updates()

    assert ("Upgrading...", "") in notify_calls
    assert any("upgrade.sh" in cmd[0] for cmd in run_calls)
