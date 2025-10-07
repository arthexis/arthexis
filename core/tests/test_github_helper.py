"""Tests for :mod:`core.github_helper`."""

from __future__ import annotations

import os
from unittest import mock

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def test_report_exception_to_github_logs_message(caplog):
    """The helper should log the queued fingerprint."""

    from core import github_helper

    caplog.set_level("INFO", logger="core.github_helper")
    github_helper.report_exception_to_github({"fingerprint": "demo"})

    assert "demo" in "".join(record.message for record in caplog.records)


def test_resolve_github_token_uses_release_manager(monkeypatch):
    """Prefer the package's release manager token when available."""

    from core import github_helper

    manager = mock.Mock(github_token="  abc123  ")
    package = mock.Mock(release_manager=manager)

    token = github_helper._resolve_github_token(package)

    assert token == "abc123"


def test_resolve_github_token_falls_back_to_environment(monkeypatch):
    """Fallback to the ``GITHUB_TOKEN`` environment variable."""

    from core import github_helper

    monkeypatch.setenv("GITHUB_TOKEN", " env-token ")
    package = mock.Mock(release_manager=None)

    token = github_helper._resolve_github_token(package)

    assert token == "env-token"


def test_resolve_github_token_raises_without_source(monkeypatch):
    """Raise a clear error when no token source is available."""

    from core import github_helper

    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    package = mock.Mock(release_manager=None)

    with pytest.raises(github_helper.GitHubRepositoryError):
        github_helper._resolve_github_token(package)
