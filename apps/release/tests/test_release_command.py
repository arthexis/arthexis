"""Regression tests for consolidated release management commands."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command


pytestmark = pytest.mark.regression


def test_release_prepare_routes_to_domain_function(monkeypatch) -> None:
    """Regression: ``release prepare`` should call ``prepare_release`` with version."""

    captured: dict[str, object] = {}

    def fake_prepare(version: str) -> None:
        captured["version"] = version

    monkeypatch.setattr("apps.release.management.commands.release.prepare_release", fake_prepare)

    call_command("release", "prepare", "1.2.3")

    assert captured["version"] == "1.2.3"


def test_release_build_routes_arguments_and_package(monkeypatch) -> None:
    """Regression: ``release build`` should pass flags and package identifier through."""

    captured: dict[str, object] = {}

    def fake_build(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("apps.release.management.commands.release.build", fake_build)
    monkeypatch.setattr("apps.release.management.commands.release.Command._get_package", lambda _self, ident: ident)

    call_command("release", "build", "--test", "--stash", "--package", "demo")

    assert captured["tests"] is True
    assert captured["stash"] is True
    assert captured["package"] == "demo"


def test_release_build_release_error_returns_exit_code(monkeypatch) -> None:
    """Regression: ``release build`` should return non-zero on release errors."""

    def fake_build(**kwargs):
        from apps.release.release import ReleaseError

        raise ReleaseError("boom")

    monkeypatch.setattr("apps.release.management.commands.release.build", fake_build)

    from apps.release.management.commands.release import Command

    result = Command().handle(action="build", bump=False, test=False, dist=False, twine=False, git=False, tag=False, all=False, force=False, stash=False, package=None)

    assert result == 1


def test_release_check_pypi_routes_to_health(monkeypatch) -> None:
    """Regression: ``release check-pypi`` should delegate to ``health`` target."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs

    monkeypatch.setattr("apps.release.management.commands.release.call_command", fake_call_command)

    command = call_command("release", "check-pypi", "1.2.3")

    assert command is None
    assert captured["name"] == "health"
    assert captured["kwargs"]["target"] == ["release.pypi"]
    assert captured["kwargs"]["release"] == "1.2.3"


def test_release_clean_logs_without_targets_raises_command_error() -> None:
    """Regression: ``release clean-logs`` should preserve command error semantics."""

    with pytest.raises(CommandError, match="Specify --all"):
        call_command("release", "clean-logs")


def test_prepare_release_wrapper_delegates_with_warning(monkeypatch) -> None:
    """Regression: legacy ``prepare_release`` should delegate and warn."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args

    monkeypatch.setattr("apps.release.management.commands.prepare_release.call_command", fake_call_command)

    stderr = StringIO()
    call_command("prepare_release", "2.0.0", stderr=stderr)

    assert captured["name"] == "release"
    assert captured["args"] == ("prepare", "2.0.0")
    assert "deprecated" in stderr.getvalue().lower()


def test_build_pypi_wrapper_delegates_flags(monkeypatch) -> None:
    """Regression: legacy ``build_pypi`` should pass through legacy flags."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("apps.release.management.commands.build_pypi.call_command", fake_call_command)

    result = call_command("build_pypi", "--test", "--stash")

    assert result == 0
    assert captured["name"] == "release"
    assert captured["args"] == ("build",)
    assert captured["kwargs"]["test"] is True
    assert captured["kwargs"]["stash"] is True


def test_capture_release_state_wrapper_delegates(monkeypatch) -> None:
    """Regression: legacy ``capture_release_state`` should delegate action and version."""

    captured: dict[str, object] = {}

    def fake_call_command(name, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args

    monkeypatch.setattr("apps.release.management.commands.capture_release_state.call_command", fake_call_command)

    call_command("capture_release_state", "3.1.4")

    assert captured["name"] == "release"
    assert captured["args"] == ("capture-state", "3.1.4")


def test_clean_release_logs_wrapper_preserves_command_error() -> None:
    """Regression: legacy ``clean_release_logs`` should still raise command errors."""

    with pytest.raises(CommandError, match="Specify --all"):
        call_command("clean_release_logs")


def test_check_pypi_wrapper_delegates(monkeypatch) -> None:
    """Regression: legacy ``check_pypi`` should delegate to ``release check-pypi``."""

    captured: dict[str, object] = {}

    def fake_call_command(*args, **kwargs):
        captured["args"] = args

    monkeypatch.setattr("apps.release.management.commands.check_pypi.call_command", fake_call_command)

    call_command("check_pypi", "42")

    assert captured["args"] == ("release", "check-pypi", "42")
