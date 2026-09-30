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
    end = deploy.index('echo "Unsupported repository dispatch type: $EVENT_TYPE"', start)
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


def test_stage_zero_activates_and_certifies_gway_without_arthexis_convergence():
    deploy = DEPLOY.read_text(encoding="utf-8")

    cleanup = deploy.index("      - name: Clean privileged Python bytecode from runner workspace")
    checkout = deploy.index("      - name: Checkout exact Arthexis deployment SHA")
    assert cleanup < checkout
    assert "if: fromJSON(env.WATCHTOWER_LEVEL) >= 0" in deploy[cleanup:checkout]

    launcher = deploy.index("      - name: Install canonical Watchtower Gway admin launcher")
    activate = deploy.index("      - name: Activate and verify canonical Gway")
    certify = deploy.index("      - name: Record accepted Gway stage")
    nginx = deploy.index("      - name: Validate baseline Nginx configuration")
    assert launcher < activate < certify < nginx

    gway_block = deploy[launcher:nginx]
    assert gway_block.count("if: fromJSON(env.WATCHTOWER_LEVEL) >= 0") >= 3
    assert "systemctl restart gway-mcp-server.service" in gway_block
    assert "systemctl restart gway-remote-auth.service" in gway_block
    assert "/var/lib/gway/venv/share/gway/sampler/survey/__main__.rx" in gway_block
    assert "/usr/local/bin/gway help survey" in gway_block
    assert 'state_path = ".watchtower/gway-accepted.json"' in gway_block
    assert '"stages": ["0-gway"]' in gway_block

    arthexis = deploy.index("      - name: Deploy Arthexis through Gway system scope")
    assert "if: fromJSON(env.WATCHTOWER_LEVEL) >= 1" in deploy[arthexis:arthexis + 180]


def test_certified_gway_is_not_rolled_back_by_later_stage_failure():
    deploy = DEPLOY.read_text(encoding="utf-8")
    rollback = deploy.index("      - name: Restore previous Gway runtime after failed deployment")
    condition = deploy[rollback:rollback + 420]

    assert "env.GWAY_DEPLOYMENT_ACCEPTED != 'true'" in condition
    assert "env.WATCHTOWER_DEPLOYMENT_ACCEPTED != 'true'" not in condition
