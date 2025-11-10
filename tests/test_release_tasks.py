import types
from datetime import datetime
from urllib.error import URLError

import pytest

import core.tasks as tasks


class CommandRecorder:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0)

    def find(self, executable: str):
        for args, kwargs in self.calls:
            if args and args[0] and args[0][0] == executable:
                return args, kwargs
        return None


def _setup_tmp(monkeypatch, tmp_path):
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    fake_file = core_dir / "tasks.py"
    fake_file.write_text("")
    monkeypatch.setattr(tasks, "__file__", str(fake_file))
    return tmp_path


@pytest.mark.role("Watchtower")
def test_no_upgrade_triggers_startup(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: "1.0")

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
    assert run_recorder.calls
    fetch_args, fetch_kwargs = run_recorder.calls[0]
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert run_recorder.find("./upgrade.sh") is None


@pytest.mark.role("Watchtower")
def test_upgrade_shows_message(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: "2.0")

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
    upgrade_call = run_recorder.find("./upgrade.sh")
    assert upgrade_call is not None
    upgrade_args, upgrade_kwargs = upgrade_call
    assert upgrade_args[0] == ["./upgrade.sh", "--no-restart"]
    assert upgrade_kwargs.get("cwd") == base
    assert upgrade_kwargs.get("check") is True
    fetch_call = run_recorder.calls[0]
    fetch_args, fetch_kwargs = fetch_call
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert scheduled
    first_call = scheduled[0]
    assert first_call["kwargs"].get("countdown") == tasks.AUTO_UPGRADE_HEALTH_DELAY_SECONDS
    assert first_call["kwargs"].get("kwargs") == {"attempt": 1}


@pytest.mark.role("Watchtower")
def test_stable_mode_skips_patch_upgrade(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.2.3")
    locks = base / "locks"
    locks.mkdir()
    (locks / "auto_upgrade.lck").write_text("stable")

    def fake_check_output(args, cwd=None, **kwargs):
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if args[:3] == ["git", "show", "origin/main:VERSION"]:
            return "1.2.4"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

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
    fetch_call = run_recorder.calls[0]
    fetch_args, fetch_kwargs = fetch_call
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert run_recorder.find("./upgrade.sh") is None


@pytest.mark.role("Watchtower")
def test_stable_mode_triggers_minor_upgrade(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.2.3")
    locks = base / "locks"
    locks.mkdir()
    (locks / "auto_upgrade.lck").write_text("stable")

    def fake_check_output(args, cwd=None, **kwargs):
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if args[:3] == ["git", "show", "origin/main:VERSION"]:
            return "1.3.0"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    notify_calls = []
    import core.notifications as notifications

    monkeypatch.setattr(
        notifications,
        "notify",
        lambda subject, body="": notify_calls.append((subject, body)),
    )

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

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
    upgrade_call = run_recorder.find("./upgrade.sh")
    assert upgrade_call is not None
    upgrade_args, upgrade_kwargs = upgrade_call
    assert upgrade_args[0] == ["./upgrade.sh", "--stable", "--no-restart"]
    assert upgrade_kwargs.get("cwd") == base
    assert upgrade_kwargs.get("check") is True
    fetch_call = run_recorder.calls[0]
    fetch_args, fetch_kwargs = fetch_call
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert scheduled
    first_call = scheduled[0]
    assert first_call["kwargs"].get("countdown") == tasks.AUTO_UPGRADE_HEALTH_DELAY_SECONDS
    assert first_call["kwargs"].get("kwargs") == {"attempt": 1}


@pytest.mark.role("Watchtower")
def test_verify_auto_upgrade_health_reverts_and_records_revision(
    monkeypatch, tmp_path
):
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

    monkeypatch.setattr(
        tasks.subprocess,
        "check_output",
        lambda *a, **k: "deadbeef",
    )

    result = tasks.verify_auto_upgrade_health.run(attempt=1)
    assert result is False
    assert not scheduled
    assert run_calls
    final_call = run_calls[-1]
    assert final_call["args"] == ["./upgrade.sh", "--revert"]
    assert final_call["cwd"] == base
    assert final_call["check"] is True

    skip_file = locks / tasks.AUTO_UPGRADE_SKIP_LOCK_NAME
    assert skip_file.exists()
    assert skip_file.read_text().strip() == "deadbeef"


@pytest.mark.role("Watchtower")
def test_check_github_updates_skips_blocked_revision(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    locks = base / "locks"
    locks.mkdir()
    (locks / "auto_upgrade.lck").write_text("latest")
    (locks / tasks.AUTO_UPGRADE_SKIP_LOCK_NAME).write_text("blocked\n")

    def fake_check_output(args, cwd=None, **kwargs):
        if args[:3] == ["git", "rev-parse", "main"]:
            return "local"
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return "blocked"
        if args[:3] == ["git", "show", "origin/main:VERSION"]:
            return "2.0"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append((args, kwargs))

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
    assert run_recorder.calls
    fetch_args, fetch_kwargs = run_recorder.calls[0]
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert run_recorder.find("./upgrade.sh") is None


def _matches_upgrade_stamp(body: str) -> bool:
    if not body.startswith("@ "):
        return False

    candidate = body[2:]

    try:
        datetime.strptime(candidate, "%Y%m%d %H:%M")
    except ValueError:
        return False
    return True
