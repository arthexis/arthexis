from __future__ import annotations

import sys

from arthexis import server


def test_cli_main_parses_standalone_server_arguments(monkeypatch):
    captured = {}

    def fake_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(server, "main", fake_main)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arthexis.server",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--data-dir",
            "/srv/arthexis",
            "--allowed-hosts",
            "example.test",
        ],
    )

    server.cli_main()

    assert captured == {
        "host": "0.0.0.0",
        "port": 9000,
        "data_dir": "/srv/arthexis",
        "allowed_hosts": "example.test",
    }


def test_watchtower_uses_native_arthexis_server_module():
    from pathlib import Path

    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
        encoding="utf-8"
    )

    native = (
        "/opt/arthexis/.venv/bin/python -m arthexis.server "
        "--host 127.0.0.1 --port 8888 "
        "--data-dir /var/lib/arthexis --allowed-hosts arthexis.com"
    )
    assert native in workflow
    assert "/opt/arthexis/.venv/bin/python -m gway" not in workflow
