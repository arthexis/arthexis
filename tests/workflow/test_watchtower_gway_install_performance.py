from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/watchtower-deploy.yml"


def workflow_text():
    return WORKFLOW.read_text()


def test_watchtower_installs_gway_candidate_once():
    text = workflow_text()
    install = 'pip install \\\n            "gway[toml] @ git+https://github.com/arthexis/gway.git@${GWAY_EXPECTED_SHA}"'
    assert text.count(install) == 1
    assert '${RUNNER_TEMP}/gway-venv' not in text
    assert '/var/lib/gway/venv.next/bin/python -m pip install' in text


def test_watchtower_verifies_exact_gway_provenance_before_swap():
    text = workflow_text()
    provenance = 'distribution.read_text("direct_url.json")'
    swap = 'sudo -n mv /var/lib/gway/venv.next /var/lib/gway/venv'
    assert provenance in text
    assert 'commit_id != expected' in text
    assert text.index(provenance) < text.index(swap)


def test_watchtower_retains_atomic_gway_rollback():
    text = workflow_text()
    assert 'sudo -n mv /var/lib/gway/venv /var/lib/gway/venv.previous' in text
    assert 'GWAY_RUNTIME_ROLLBACK_AVAILABLE=true' in text
    assert 'GWAY_RUNTIME_SWAPPED=true' in text
    assert 'sudo -n mv /var/lib/gway/venv.previous /var/lib/gway/venv' in text


def test_watchtower_emits_canonical_gway_subphase_timings():
    text = workflow_text()
    for phase in (
        "canonical-gway-venv",
        "canonical-gway-pip",
        "canonical-gway-install",
        "canonical-gway-pre-swap-verify",
        "canonical-gway-swap",
        "canonical-gway-post-swap-verify",
    ):
        assert phase in text
    assert "watchtower_timing phase=%s seconds=%d.%03d" in text


def test_arthexis_install_uses_verified_canonical_gway_with_timing():
    text = workflow_text()
    assert '/var/lib/gway/venv/bin/gway -t install . --system --force' in text
