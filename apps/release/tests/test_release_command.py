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
