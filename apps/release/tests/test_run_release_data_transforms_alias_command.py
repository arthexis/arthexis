"""Tests for the supported run_release_data_transforms alias command."""

from __future__ import annotations

from unittest.mock import ANY

from django.core.management import call_command


def test_run_release_data_transforms_alias_remains_supported(monkeypatch, capsys) -> None:
    """The flat alias should remain a supported synonym for the release subcommand."""

    forwarded: dict[str, object] = {}

    def fake_call_command(*args, **kwargs):
        forwarded["args"] = args
        forwarded["kwargs"] = kwargs

    monkeypatch.setattr(
        "apps.release.management.commands.run_release_data_transforms.call_command",
        fake_call_command,
    )

    call_command("run_release_data_transforms", "sync-users", max_batches=3)

    assert forwarded["args"] == ("release", "run-data-transforms", "sync-users")
    assert forwarded["kwargs"] == {
        "max_batches": 3,
        "stdout": ANY,
        "stderr": ANY,
    }
    assert "supported alias" in capsys.readouterr().out
