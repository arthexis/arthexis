"""Regression tests for consolidated release management commands."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.release import DEFAULT_PACKAGE


@pytest.mark.parametrize("action", ["clean-logs", "clean"])
def test_release_clean_logs_without_targets_raises_command_error(action: str) -> None:
    """Regression: clean aliases should preserve command error semantics."""

    with pytest.raises(CommandError, match="Specify --all"):
        call_command("release", action)


@pytest.mark.parametrize("action", ["run-data-transforms", "transforms", "xforms"])
def test_release_run_data_transforms_invokes_all_registered(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """Regression: transform aliases should run all discovered transforms."""

    monkeypatch.setattr(
        "apps.release.management.commands.release.list_transform_names",
        lambda: ["first", "second"],
    )

    captured: list[tuple[str, int]] = []

    def fake_runner(self, name: str, *, max_batches: int) -> None:
        captured.append((name, max_batches))

    monkeypatch.setattr(
        "apps.release.management.commands.release.Command._run_transform_batches",
        fake_runner,
    )

    call_command("release", action, "--max-batches", "2")

    assert captured == [("first", 2), ("second", 2)]


@pytest.mark.parametrize("action", ["capture-state", "snapshot", "snap"])
def test_release_snapshot_alias_dispatches_to_capture_state(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """Regression: snapshot aliases should dispatch to capture-state handler."""

    captured_versions: list[str] = []

    monkeypatch.setattr(
        "apps.release.management.commands.release.capture_migration_state",
        lambda version: captured_versions.append(version) or f"/tmp/{version}",
    )

    call_command("release", action, "2026.03")

    assert captured_versions == ["2026.03"]


@pytest.mark.parametrize("action", ["apply-migrations", "migrate"])
def test_release_apply_migrations_alias_dispatches(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """Regression: migrate aliases should resolve to the canonical handler."""

    events: list[tuple[str, tuple[object, ...]]] = []

    monkeypatch.setattr(
        "apps.release.management.commands.release.Command._resolve_installed_version",
        lambda self, explicit_version: "1.0.0",
    )
    monkeypatch.setattr(
        "apps.release.management.commands.release.Command._resolve_bundle_dir",
        lambda self, target_version, explicit_dir: "/tmp/bundle",
    )
    monkeypatch.setattr(
        "apps.release.management.commands.release.Command._verify_bundle",
        lambda self, bundle_dir: events.append(("verify", (bundle_dir,))),
    )
    monkeypatch.setattr(
        "apps.release.management.commands.release.call_command",
        lambda *args, **kwargs: events.append(("call_command", args)),
    )
    monkeypatch.setattr(
        "apps.release.management.commands.release.Command._run_deferred_data_transforms",
        lambda self, *, skip: events.append(("transforms", (skip,))),
    )

    call_command("release", action, "1.0.0")

    assert events == [
        ("verify", ("/tmp/bundle",)),
        ("call_command", ("migrate", "--noinput")),
        ("call_command", ("migrate", "--check")),
        ("transforms", (False,)),
    ]


def test_release_build_mode_release_enables_common_operator_flags(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    """Regression: build presets should enable the expected common workflow flags."""

    settings.LOG_DIR = "/tmp"
    output = StringIO()
    captured_options: dict[str, object] = {}

    monkeypatch.setattr(
        "apps.release.management.commands.release.build",
        lambda **kwargs: captured_options.update(kwargs),
    )

    call_command("release", "build", "--mode", "release", stdout=output)

    assert captured_options == {
        "all": False,
        "bump": False,
        "dist": True,
        "force": False,
        "git": True,
        "package": DEFAULT_PACKAGE,
        "stash": False,
        "tag": True,
        "tests": True,
        "twine": True,
    }
