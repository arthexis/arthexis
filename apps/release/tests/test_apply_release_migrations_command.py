"""Regression tests for release migration bundle application command."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import ANY

import pytest
from django.core.management import call_command


def _write_bundle(base_dir: Path, target_version: str, installed_version: str) -> Path:
    """Create a minimal release migration bundle fixture on disk."""

    bundle_dir = base_dir / "releases" / target_version / "migrations"
    manifests_dir = bundle_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = manifests_dir / f"{installed_version}__to__{target_version}.json"
    manifest_payload = {
        "from_version": installed_version,
        "to_version": target_version,
        "deltas": {"demoapp": ["0002_auto"]},
    }
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    checksum_entry = (
        f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  "
        f"{manifest_path.relative_to(bundle_dir).as_posix()}"
    )
    checksum_path = bundle_dir / "checksums.sha256"
    checksum_path.write_text(checksum_entry + "\n", encoding="utf-8")

    return bundle_dir


def test_apply_release_migrations_uses_manifest_delta(monkeypatch, settings, tmp_path) -> None:
    """Regression: command should apply only app targets declared in the bundle manifest."""

    settings.BASE_DIR = tmp_path
    installed_version = "1.0.0"
    target_version = "1.1.0"
    (tmp_path / "VERSION").write_text(installed_version + "\n", encoding="utf-8")
    bundle_dir = _write_bundle(tmp_path, target_version, installed_version)

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_call_command(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("apps.release.management.commands.release.call_command", fake_call_command)

    call_command("release", "apply-migrations", target_version, bundle_dir=str(bundle_dir))

    assert calls[0][0] == ("migrate", "demoapp", "0002_auto", "--noinput")
    assert calls[1][0] == ("migrate", "--check")
    assert calls[2][0] == ("release", "run-data-transforms", "--max-batches", "1")


def test_apply_release_migrations_same_version_still_syncs_db(monkeypatch, settings, tmp_path) -> None:
    """Regression: same-version invocation should still ensure database migration state."""

    settings.BASE_DIR = tmp_path
    version = "3.0.0"
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")
    bundle_dir = _write_bundle(tmp_path, version, version)

    calls: list[tuple[object, ...]] = []

    def fake_call_command(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("apps.release.management.commands.release.call_command", fake_call_command)

    call_command("release", "apply-migrations", version, bundle_dir=str(bundle_dir))

    assert ("migrate", "--noinput") in calls
    assert ("migrate", "--check") in calls
    assert ("release", "run-data-transforms", "--max-batches", "1") in calls
    assert not any(len(call) > 1 and call[0] == "migrate" and call[1] == "demoapp" for call in calls)


def test_apply_release_migrations_falls_back_on_bundle_mismatch(monkeypatch, settings, tmp_path) -> None:
    """Regression: command should fall back to migrate when manifest is unavailable."""

    settings.BASE_DIR = tmp_path
    installed_version = "1.0.0"
    target_version = "1.1.0"
    (tmp_path / "VERSION").write_text(installed_version + "\n", encoding="utf-8")

    bundle_dir = tmp_path / "releases" / target_version / "migrations"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "checksums.sha256").write_text("", encoding="utf-8")

    calls: list[tuple[object, ...]] = []

    def fake_call_command(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("apps.release.management.commands.release.call_command", fake_call_command)

    call_command("release", "apply-migrations", target_version, bundle_dir=str(bundle_dir))

    assert ("migrate", "--noinput") in calls
    assert ("migrate", "--check") in calls
    assert ("release", "run-data-transforms", "--max-batches", "1") in calls


def test_apply_release_migrations_validates_signature_when_key_is_set(monkeypatch, settings, tmp_path) -> None:
    """Regression: command should validate checksum signatures when a signing key is configured."""

    settings.BASE_DIR = tmp_path
    installed_version = "2.0.0"
    target_version = "2.1.0"
    (tmp_path / "VERSION").write_text(installed_version + "\n", encoding="utf-8")
    bundle_dir = _write_bundle(tmp_path, target_version, installed_version)

    signing_key = "test-signing-key"
    checksums_path = bundle_dir / "checksums.sha256"
    signature = hmac.new(signing_key.encode("utf-8"), checksums_path.read_bytes(), hashlib.sha256).hexdigest()
    (bundle_dir / "checksums.sha256.sig").write_text(signature + "\n", encoding="utf-8")

    monkeypatch.setenv("RELEASE_BUNDLE_SIGNING_KEY", signing_key)

    calls: list[tuple[object, ...]] = []

    def fake_call_command(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("apps.release.management.commands.release.call_command", fake_call_command)

    call_command("release", "apply-migrations", target_version, bundle_dir=str(bundle_dir))

    assert ("migrate", "demoapp", "0002_auto", "--noinput") in calls


def test_apply_release_migrations_skips_data_transforms_when_requested(monkeypatch, settings, tmp_path) -> None:
    """Regression: command should skip deferred transforms when explicitly requested."""

    settings.BASE_DIR = tmp_path
    installed_version = "1.0.0"
    target_version = "1.1.0"
    (tmp_path / "VERSION").write_text(installed_version + "\n", encoding="utf-8")
    bundle_dir = _write_bundle(tmp_path, target_version, installed_version)

    calls: list[tuple[object, ...]] = []

    def fake_call_command(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("apps.release.management.commands.release.call_command", fake_call_command)

    call_command(
        "release",
        "apply-migrations",
        target_version,
        bundle_dir=str(bundle_dir),
        skip_data_transforms=True,
    )

    assert ("migrate", "demoapp", "0002_auto", "--noinput") in calls
    assert ("migrate", "--check") in calls
    assert ("release", "run-data-transforms", "--max-batches", "1") not in calls


def test_apply_release_migrations_alias_remains_supported(monkeypatch, capsys) -> None:
    """The flat alias should remain a supported synonym for the release subcommand."""

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
