from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.core.management.commands.env import read_env
from apps.repos.models import GitHubToken


@pytest.mark.django_db
def test_github_set_token_user_stores_after_validation(monkeypatch):
    user_model = get_user_model()
    user = user_model.objects.create_user(username="alice", password="x")
    monkeypatch.setattr("apps.repos.services.github.validate_token", lambda _token: (True, "ok", "octocat"))

    with patch("apps.repos.management.commands.github.getpass", return_value="tok"), patch(
        "builtins.input", return_value="y"
    ):
        call_command("github", "set-token", "--user", user.username)

    stored = GitHubToken.objects.get(user=user)
    assert stored.label == "octocat"
    assert stored.token == "tok"


@pytest.mark.django_db
def test_github_set_token_global_stores_in_env(monkeypatch, tmp_path):
    env_file = tmp_path / "arthexis.env"
    monkeypatch.setattr("apps.repos.management.commands.github.env_path", lambda: env_file)
    monkeypatch.setattr("apps.repos.services.github.validate_token", lambda _token: (True, "ok", ""))

    with patch("apps.repos.management.commands.github.getpass", return_value="tok2"), patch(
        "builtins.input", return_value="yes"
    ):
        call_command("github", "set-token", "--global")

    assert read_env(env_file).get("GITHUB_TOKEN") == "tok2"
