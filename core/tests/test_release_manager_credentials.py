"""Tests for release manager credential helpers."""

from __future__ import annotations

import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.models import ReleaseManager


@pytest.mark.parametrize(
    "token,expected_username",
    [
        ("ghp_example", "x-access-token"),
        ("  ghp_trimmed  ", "x-access-token"),
    ],
)
def test_to_git_credentials_defaults_username(token: str, expected_username: str) -> None:
    manager = ReleaseManager(github_token=token)

    creds = manager.to_git_credentials()

    assert creds is not None
    assert creds.username == expected_username
    assert creds.password == token.strip()


def test_to_git_credentials_respects_explicit_username() -> None:
    manager = ReleaseManager(github_token="ghp_example", git_username="octocat")

    creds = manager.to_git_credentials()

    assert creds is not None
    assert creds.username == "octocat"
    assert creds.password == "ghp_example"
