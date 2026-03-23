"""Minimal regression tests for release migration alias support."""

from __future__ import annotations

from unittest.mock import ANY

from django.core.management import call_command


def test_apply_release_migrations_alias_remains_supported(monkeypatch, capsys) -> None:
    """Keep the flat alias wired to the consolidated release command."""

    forwarded: dict[str, object] = {}

    def fake_call_command(*args, **kwargs):
        forwarded["args"] = args
        forwarded["kwargs"] = kwargs

    monkeypatch.setattr(
        "apps.release.management.commands.apply_release_migrations.call_command",
        fake_call_command,
    )

    call_command(
        "apply_release_migrations",
        "2026.03",
        installed_version="2026.02",
        bundle_dir="/tmp/bundle",
        strict=True,
        skip_data_transforms=True,
    )

    assert forwarded["args"] == ("release", "apply-migrations", "2026.03")
    assert forwarded["kwargs"] == {
        "installed_version": "2026.02",
        "bundle_dir": "/tmp/bundle",
        "strict": True,
        "skip_data_transforms": True,
        "stdout": ANY,
        "stderr": ANY,
    }
    assert "supported alias" in capsys.readouterr().out
