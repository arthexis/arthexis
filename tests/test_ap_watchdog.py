from pathlib import Path

import pytest

from scripts import ap_watchdog


class DummyRunner:
    def __init__(self):
        self.calls = []
        self.is_active_calls = 0

    def run(self, args, check: bool = False):  # noqa: ANN001
        self.calls.append(list(args))
        cmd = args[0]
        if cmd == "systemctl" and len(args) > 1 and args[1] == "is-active":
            self.is_active_calls += 1
            # First four calls report inactive
            return ap_watchdog.CommandResult(1)
        if cmd == "sudo" and len(args) > 1 and args[1] == "systemctl":
            return ap_watchdog.CommandResult(0)
        if cmd == "nmcli":
            return ap_watchdog.CommandResult(1)
        if cmd == "ping":
            return ap_watchdog.CommandResult(1)
        return ap_watchdog.CommandResult(0)


class SnapshotRunner:
    def __init__(self):
        self.calls = []

    def run(self, args, check: bool = False):  # noqa: ANN001
        self.calls.append(list(args))
        if args[:3] == ["nmcli", "-t", "-f"]:
            return ap_watchdog.CommandResult(0, "home:wifi\nbase:ethernet")
        if args[:4] == ["nmcli", "-g", "connection.interface-name"]:
            return ap_watchdog.CommandResult(0, "wlan1")
        if args[:4] == ["nmcli", "-g", "802-11-wireless.mode"]:
            return ap_watchdog.CommandResult(0, "ap")
        return ap_watchdog.CommandResult(0)


@pytest.mark.django_db
def test_reload_watchdog_command(monkeypatch, settings, tmp_path):
    settings.BASE_DIR = tmp_path

    regenerated = {}

    def fake_snapshot(base_dir: Path, runner=None):
        regenerated["base_dir"] = base_dir
        return [ap_watchdog.ConnectionTemplate("home", "wlan1", "wifi")]

    monkeypatch.setattr(ap_watchdog, "snapshot_nmcli_template", fake_snapshot)

    from django.core.management import call_command

    call_command("reload_watchdog")

    assert regenerated["base_dir"] == tmp_path


def test_watchdog_restarts_after_downtime(tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "service.lck").write_text("demo\n")
    (lock_dir / "systemd_services.lck").write_text("demo.service\n")

    watchdog = ap_watchdog.APWatchdog(tmp_path, runner=DummyRunner())

    for _ in range(4):
        watchdog.run_once()

    log_file = tmp_path / "logs" / "ap-watchdog.log"
    content = log_file.read_text()
    assert "Restarted demo.service" in content


def test_snapshot_writes_template(tmp_path):
    runner = SnapshotRunner()
    ap_watchdog.snapshot_nmcli_template(tmp_path, runner=runner)

    data = (tmp_path / ".locks" / "ap_watchdog_template.json").read_text()
    assert "home" in data
    assert "ap_watchdog.lck" in {p.name for p in (tmp_path / ".locks").iterdir()}
