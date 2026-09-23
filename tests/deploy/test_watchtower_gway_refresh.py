from pathlib import Path


def test_watchtower_gway_refresh_dispatches_only_when_dependency_is_stale() -> None:
    workflow = Path(".github/workflows/watchtower-gway-refresh.yml").read_text(
        encoding="utf-8"
    )

    assert "cron: '*/10 * * * *'" in workflow
    assert "git ls-remote https://github.com/arthexis/gway.git refs/heads/main" in workflow
    assert 'distribution("gway").read_text("direct_url.json")' in workflow
    assert 'gway_refresh=up_to_date' in workflow
    assert "watchtower-deploy.yml/dispatches" in workflow
    assert '"ref":"main"' in workflow
    assert "actions: write" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow


def test_watchtower_gway_refresh_does_not_require_cross_repo_secret() -> None:
    workflow = Path(".github/workflows/watchtower-gway-refresh.yml").read_text(
        encoding="utf-8"
    )

    assert "secrets." not in workflow
    assert "PAT" not in workflow
    assert "repository_dispatch" not in workflow
