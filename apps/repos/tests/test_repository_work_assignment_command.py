from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_repo_assignments_pull_upstream_rejects_negative_timeout():
    with pytest.raises(CommandError, match="--timeout"):
        call_command(
            "repo_assignments",
            "pull-upstream",
            "--url",
            "https://upstream.example",
            "--token",
            "sync-token",
            "--timeout",
            "-1",
        )
