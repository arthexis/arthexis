from scripts.watchtower_outcome import WatchtowerObservation, classify


def test_cancelled_run_with_successor_is_superseded():
    assert classify(WatchtowerObservation("cancelled", successor_exists=True)) == "superseded"


def test_cancelled_run_without_successor_is_actionable():
    assert classify(WatchtowerObservation("cancelled")) == "actionable"


def test_transient_stage_failure_is_pending_while_retrying():
    assert classify(WatchtowerObservation("failure", stage_retrying=True)) == "pending"


def test_exhausted_stage_failure_is_actionable():
    assert classify(WatchtowerObservation("failure")) == "actionable"


def test_accepted_deployment_without_release_intent_is_normal():
    assert classify(
        WatchtowerObservation("success", deployment_accepted=True, release_requested=False)
    ) == "normal"


def test_requested_release_waits_for_publisher():
    assert classify(
        WatchtowerObservation("success", deployment_accepted=True, release_requested=True)
    ) == "pending"


def test_requested_release_with_successful_publisher_is_normal():
    assert classify(
        WatchtowerObservation(
            "success",
            deployment_accepted=True,
            release_requested=True,
            publisher_conclusion="success",
        )
    ) == "normal"


def test_release_evidence_conflict_is_always_actionable():
    assert classify(
        WatchtowerObservation(
            "success",
            deployment_accepted=True,
            release_requested=False,
            evidence_conflict=True,
        )
    ) == "actionable"
