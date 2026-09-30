from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

DEPLOY = Path(".github/workflows/watchtower-deploy.yml")
DEPENDENCIES = Path(".github/workflows/watchtower-dependencies.yml")
RESOLVER = Path(".github/scripts/watchtower_dependency.py")


def test_queue_ignores_prs_waiting_for_watchtower():
    text = DEPLOY.read_text(encoding="utf-8")

    assert '. == "needs-watchtower"' in text
    assert "Coalesce active PR queue" in text


def test_successful_watchtower_acceptance_wakes_dependency_reconcilers():
    text = DEPLOY.read_text(encoding="utf-8")

    assert "Reconcile PRs waiting on Watchtower" in text
    assert "watchtower-accepted" in text
    assert "arthexis/arthexis arthexis/gway" in text
    assert "RELEASE_AUTOMATION_TOKEN" in text


def test_dependency_reconciler_parks_and_releases_native_auto_merge():
    text = DEPENDENCIES.read_text(encoding="utf-8")

    assert "--disable-auto" in text
    assert "--auto --squash" in text
    assert "needs-watchtower" in text


def test_dependency_resolver_rejects_cycles_and_checks_accepted_ancestry():
    text = RESOLVER.read_text(encoding="utf-8")

    assert "Watchtower dependency cycle" in text
    assert 'comparison.get("status") in {"identical", "ahead"}' in text
