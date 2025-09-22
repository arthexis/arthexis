import types
from datetime import datetime
from urllib.error import URLError

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
def test_no_upgrade_triggers_startup(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")

    def fake_run(*args, **kwargs):
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: b"1.0")

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    called = {}
    import nodes.apps as nodes_apps

    monkeypatch.setattr(
        nodes_apps, "_startup_notification", lambda: called.setdefault("x", True)
    )

    tasks.check_github_updates()

    assert called.get("x")
    assert scheduled == []


@pytest.mark.role("Constellation")
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

    monkeypatch.setattr(
        notifications,
        "notify",
        lambda subject, body="": notify_calls.append((subject, body)),
    )

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    assert any(
        subject == "Upgrading..."
        and _matches_upgrade_stamp(body)
        for subject, body in notify_calls
    )
    assert any("upgrade.sh" in cmd[0] for cmd in run_calls)
    assert scheduled
    first_call = scheduled[0]
    assert first_call["kwargs"].get("countdown") == tasks.AUTO_UPGRADE_HEALTH_DELAY_SECONDS
    assert first_call["kwargs"].get("kwargs") == {"attempt": 1}


@pytest.mark.role("Constellation")
def test_verify_auto_upgrade_health_retries_and_reverts(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    locks = base / "locks"
    locks.mkdir()

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    def fake_urlopen(*args, **kwargs):
        raise URLError("down")

    monkeypatch.setattr(tasks.urllib.request, "urlopen", fake_urlopen)

    run_calls = []

    def fake_run(args, cwd=None, check=None):
        run_calls.append({"args": args, "cwd": cwd, "check": check})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)

    tasks.verify_auto_upgrade_health.run(attempt=1)
    assert scheduled
    assert scheduled[-1]["kwargs"].get("kwargs") == {"attempt": 2}
    assert scheduled[-1]["kwargs"].get("countdown") == tasks.AUTO_UPGRADE_HEALTH_DELAY_SECONDS

    scheduled.clear()
    tasks.verify_auto_upgrade_health.run(attempt=2)
    assert scheduled
    assert scheduled[-1]["kwargs"].get("kwargs") == {"attempt": 3}

    scheduled.clear()
    tasks.verify_auto_upgrade_health.run(attempt=3)
    assert not scheduled
    assert run_calls
    final_call = run_calls[-1]
    assert final_call["args"] == ["./upgrade.sh", "--revert"]
    assert final_call["cwd"] == base


def _matches_upgrade_stamp(body: str) -> bool:
    if not body.startswith("@ "):
        return False

    candidate = body[2:]

    try:
        datetime.strptime(candidate, "%Y%m%d %H:%M")
    except ValueError:
        return False
    return True
