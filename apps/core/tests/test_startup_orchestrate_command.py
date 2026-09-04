import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.core.management.commands.startup_orchestrate import Command

pytestmark = [pytest.mark.gate_upgrade]


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
