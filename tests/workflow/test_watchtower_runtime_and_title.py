from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

DEPLOY = Path(".github/workflows/watchtower-deploy.yml")
CANDIDATE = Path(".github/workflows/watchtower-candidate.yml")
INSTALL = Path("install.sh")


def test_watchtower_push_dispatch_uses_subject_only_for_run_name():
    candidate = CANDIDATE.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")

    assert 'run_name="$(git show -s --format=%s "$AFTER_SHA")"' in candidate
    assert "-f event_type='arthexis-candidate'" in candidate
    assert '-f client_payload[run_name]="$RUN_NAME"' in candidate
    assert "types: [gway-candidate, arthexis-candidate]" in deploy
    assert "github.event.head_commit.message" not in deploy
    assert "push:" not in deploy.split("on:", 1)[1].split("permissions:", 1)[0]


def test_arthexis_candidate_dispatch_converges_with_current_gway():
    deploy = DEPLOY.read_text(encoding="utf-8")
    start = deploy.index('elif [[ "$EVENT_TYPE" == "arthexis-candidate" ]]')
    end = deploy.index("          else", start)
    block = deploy[start:end]

    assert 'arthexis_sha="$REQUESTED_ARTHEXIS_SHA"' in block
    assert 'arthexis_sha="$current_arthexis"' in block
    assert 'gway_sha="$current_gway"' in block
    assert 'source="arthexis-coalesced"' in block


def test_install_accepts_externally_managed_pipless_runtime():
    script = INSTALL.read_text(encoding="utf-8")

    assert 'venv_created=false' in script
    assert '"$venv_dir/bin/python" -m pip --version' in script
    assert 'elif [[ "$venv_created" == true ]]' in script
    assert "Using externally managed Arthexis virtual environment" in script
