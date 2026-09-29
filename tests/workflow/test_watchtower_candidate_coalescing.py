from pathlib import Path


WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def _pair_resolution_block() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: Resolve exact deployment pair")
    end = text.index("      - name: Checkout exact Arthexis deployment SHA", start)
    return text[start:end]


def test_superseded_gway_candidate_coalesces_to_current_main():
    block = _pair_resolution_block()

    assert 'if [[ "$REQUESTED_GWAY_SHA" == "$current_gway" ]]' in block
    assert "repos/arthexis/gway/compare/$REQUESTED_GWAY_SHA...$current_gway" in block
    assert '[[ "$compare_status" != "ahead" ]]' in block
    assert 'watchtower_resolution=superseded' in block
    assert 'gway_sha="$current_gway"' in block
    assert 'source="gway-coalesced"' in block


def test_divergent_gway_candidate_still_fails_resolution():
    block = _pair_resolution_block()

    assert "Gway candidate is not an ancestor of current main" in block
    assert 'test "$REQUESTED_GWAY_SHA" = "$current_gway"' not in block
