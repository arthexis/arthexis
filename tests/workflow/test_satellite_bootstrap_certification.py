from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def test_stage_one_verifies_satellite_bootstrap_from_exact_candidate_sources():
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: Verify satellite bootstrap from candidate sources")
    end = text.index("      - name: Preflight Watchtower Remote stage", start)
    block = text[start:end]

    assert "ARTHEXIS_BOOTSTRAP_VERIFY_ONLY=1" in block
    assert "GWAY_BOOTSTRAP_GWAY=/usr/local/bin/gway" in block
    assert 'ARTHEXIS_BOOTSTRAP_SOURCE="$GITHUB_WORKSPACE"' in block
    assert 'ARTHEXIS_BOOTSTRAP_SHA="$ARTHEXIS_EXPECTED_SHA"' in block
    assert "UV_OFFLINE=1" not in block
    assert "PIP_NO_INDEX=1" not in block
    assert 'ARTHEXIS_BOOTSTRAP_SOURCE="$GITHUB_WORKSPACE"' in block
    assert "install.arthexis.com" not in block
    assert "github.com/arthexis/arthexis/archive/" not in block
    assert "var/db.sqlite3" in block


def test_stage_two_only_checks_public_candidate_identity():
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: Verify public satellite package identity")
    end = text.index("      - name: Preflight Watchtower Wire capability", start)
    block = text[start:end]

    assert "SATELLITE_BOOTSTRAP_TESTED_GWAY_SHA" in block
    assert "SATELLITE_BOOTSTRAP_TESTED_ARTHEXIS_SHA" in block
    assert "install.arthexis.com/satellite" in block
    assert "install.arthexis.com/gway" in block
    assert "github.com/arthexis/gway/archive/$GWAY_EXPECTED_SHA.tar.gz" in block
    assert "github.com/arthexis/arthexis/archive/$ARTHEXIS_EXPECTED_SHA.tar.gz" in block
    assert "ARTHEXIS_BOOTSTRAP_VERIFY_ONLY=1" not in block
