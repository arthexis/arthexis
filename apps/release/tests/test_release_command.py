"""Regression tests for consolidated release management commands."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import CommandError, call_command

from apps.release import DEFAULT_PACKAGE
from apps.release.services.builder import build
from apps.release.services.models import Credentials


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


def test_builder_release_uploads_before_pushing_git_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Regression: release uploads must complete before branch/tag pushes happen."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("django\n", encoding="utf-8")

    events: list[tuple[str, object]] = []

    monkeypatch.setattr("apps.release.services.builder._git_clean", lambda: True)
    monkeypatch.setattr("apps.release.services.builder._git_has_staged_changes", lambda: True)
    monkeypatch.setattr(
        "apps.release.services.builder._write_pyproject",
        lambda package, version, requirements: events.append(
            ("write_pyproject", (package.name, version, tuple(requirements)))
        ),
    )
    monkeypatch.setattr(
        "apps.release.services.builder._build_in_sanitized_tree",
        lambda base_dir, *, generate_wheels: (
            (base_dir / "dist").mkdir(exist_ok=True),
            (base_dir / "dist" / "package.tar.gz").write_text("artifact", encoding="utf-8"),
            events.append(("build_dist", generate_wheels)),
        )[-1],
    )
    monkeypatch.setattr(
        "apps.release.services.network.fetch_pypi_releases",
        lambda package: {},
    )
    monkeypatch.setattr(
        "apps.release.services.uploader.upload_with_retries",
        lambda cmd, *, repository: events.append(("upload", tuple(cmd))),
    )
    monkeypatch.setattr(
        "apps.release.services.builder._run",
        lambda cmd, check=True, cwd=None: events.append(("run", tuple(cmd))) or SimpleNamespace(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "build",
        SimpleNamespace(__file__="/tmp/site-packages/build/__init__.py"),
    )

    build(
        dist=True,
        twine=True,
        git=True,
        tag=True,
        package=DEFAULT_PACKAGE,
        creds=Credentials(token="token"),
    )

    upload_index = events.index(
        next(event for event in events if event[0] == "upload")
    )
    branch_push_index = events.index(("run", ("git", "push")))
    tag_push_index = events.index(("run", ("git", "push", "origin", "v1.2.3")))

    assert upload_index < branch_push_index
    assert upload_index < tag_push_index
