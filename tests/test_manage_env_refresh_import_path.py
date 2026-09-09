from __future__ import annotations

import os
from pathlib import Path

import manage


def test_run_env_refresh_prefers_checkout_on_pythonpath(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    inherited_path = "/opt/arthexis/.venv/lib/python/site-packages"
    monkeypatch.setenv("PYTHONPATH", inherited_path)

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

    monkeypatch.setattr(manage.subprocess, "run", fake_run)

    manage._run_env_refresh(tmp_path)

    kwargs = captured["kwargs"]
    env = kwargs["env"]
    assert env["PYTHONPATH"].split(os.pathsep) == [str(tmp_path), inherited_path]
    assert kwargs["cwd"] == tmp_path
