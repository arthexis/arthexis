import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from django.core.management import call_command

from apps.core.management.commands.startup_orchestrate import Command
from gate_markers import gate


pytestmark = [gate.upgrade]


def _invoke_startup_orchestrate(
    tmp_path: Path,
    monkeypatch,
    *,
    extra_args: list[str] | None = None,
    lcd_enabled: bool = True,
    startup_message: str = "queued:{port}",
):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "apps.core.management.commands.startup_orchestrate.Command._run_preflight",
        lambda self, lock_dir, base_dir: (
            True,
            {"name": "runserver_preflight", "status": "ok", "detail": "ok"},
        ),
    )
    monkeypatch.setattr(
        "apps.core.management.commands.startup_orchestrate.Command._run_startup_maintenance",
        lambda self: (
            True,
            {"name": "startup_maintenance", "status": "ok", "detail": "ok"},
        ),
    )
    monkeypatch.setattr(
        "apps.core.management.commands.startup_orchestrate.send_startup_net_message",
        lambda port=None: startup_message.format(port=port),
    )
    monkeypatch.setattr(
        "apps.core.management.commands.startup_orchestrate.lcd_feature_enabled",
        lambda value: lcd_enabled,
    )
    monkeypatch.setattr(
        "apps.core.management.commands.startup_orchestrate._read_service_mode",
        lambda value: "embedded",
    )

    stdout = StringIO()
    args = [
        "startup_orchestrate",
        "--port",
        "8899",
        "--lock-dir",
        str(lock_dir),
    ]
    if extra_args:
        args.extend(extra_args)

    call_command(*args, stdout=stdout)
    return lock_dir, json.loads(stdout.getvalue())


def test_startup_orchestrate_outputs_json_contract_and_writes_locks(tmp_path, monkeypatch):
    lock_dir, payload = _invoke_startup_orchestrate(tmp_path, monkeypatch)

    assert payload["status"] == "ok"
    assert payload["launch"]["celery_embedded"] is True
    assert payload["launch"]["lcd_embedded"] is True
    assert payload["startup_message_status"] == "queued:8899"

    started_at = (lock_dir / "startup_started_at.lck").read_text(encoding="utf-8").strip()
    assert started_at.isdigit()

    duration_payload = json.loads((lock_dir / "startup_orchestrate_status.lck").read_text(encoding="utf-8"))
    assert duration_payload["phase"] == "orchestration"
    assert duration_payload["status"] == 0


def test_startup_orchestrate_uses_systemd_decisions_when_requested(tmp_path, monkeypatch):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite", encoding="utf-8")
    (lock_dir / "systemd_services.lck").write_text(
        "celery-suite.service\ncelery-beat-suite.service\nlcd-suite.service\n",
        encoding="utf-8",
    )
    _, payload = _invoke_startup_orchestrate(
        tmp_path,
        monkeypatch,
        extra_args=[
            "--service-mode",
            "systemd",
            "--celery-mode",
            "systemd",
        ],
        startup_message="queued:ok",
    )
    assert payload["launch"]["celery_embedded"] is False
    assert payload["launch"]["lcd_embedded"] is False
    assert payload["launch"]["lcd_target_mode"] == "systemd"


def test_startup_orchestrate_skips_lcd_when_feature_disabled(tmp_path, monkeypatch):
    _, payload = _invoke_startup_orchestrate(
        tmp_path,
        monkeypatch,
        lcd_enabled=False,
    )
    assert payload["startup_message_status"] == "skipped:lcd-disabled"
    assert payload["launch"]["lcd_embedded"] is False


def test_run_preflight_passes_current_python_to_helper(tmp_path, monkeypatch):
    base_dir = tmp_path / "base"
    helper_dir = base_dir / "scripts" / "helpers"
    helper_dir.mkdir(parents=True, exist_ok=True)
    (helper_dir / "runserver_preflight.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    captured = {}

    def _fake_run(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("apps.core.management.commands.startup_orchestrate.subprocess.run", _fake_run)

    ok, status = Command()._run_preflight(lock_dir=lock_dir, base_dir=base_dir)

    assert ok is True
    assert status["status"] == "ok"
    assert captured["env"]["ARTHEXIS_PYTHON_BIN"] == sys.executable
