import pytest

from apps.ocpp.simulator.authorization import (
    AuthorizationAttempt,
    AuthorizationAttemptResult,
    AuthorizationScenario,
)


def test_authorization_scenario_preserves_order_and_repeat_identity():
    scenario = AuthorizationScenario(
        name="restricted-matrix",
        policy_context="restricted",
        attempts=(
            AuthorizationAttempt(name="known", id_tag="KNOWN"),
            AuthorizationAttempt(name="unknown", id_tag="UNKNOWN"),
            AuthorizationAttempt(
                name="known-repeat",
                id_tag="KNOWN",
                repeat_of="known",
            ),
        ),
    )

    assert [attempt.name for attempt in scenario.attempts] == [
        "known",
        "unknown",
        "known-repeat",
    ]
    assert scenario.attempts[2].repeat_of == "known"
    assert scenario.policy_context == "restricted"


def test_repeat_must_reference_an_earlier_attempt():
    with pytest.raises(ValueError, match="earlier attempt"):
        AuthorizationScenario(
            name="invalid-repeat",
            attempts=(
                AuthorizationAttempt(
                    name="repeat",
                    id_tag="KNOWN",
                    repeat_of="original",
                ),
                AuthorizationAttempt(name="original", id_tag="KNOWN"),
            ),
        )


def test_attempt_names_are_unique_within_scenario():
    with pytest.raises(ValueError, match="must be unique"):
        AuthorizationScenario(
            name="duplicate-names",
            attempts=(
                AuthorizationAttempt(name="known", id_tag="FIRST"),
                AuthorizationAttempt(name="known", id_tag="SECOND"),
            ),
        )


def test_result_contract_does_not_echo_raw_id_tag():
    result = AuthorizationAttemptResult(
        scenario="restricted-matrix",
        attempt="known",
        sequence=1,
        status="Accepted",
        policy_context="restricted",
    )

    assert result == AuthorizationAttemptResult(
        scenario="restricted-matrix",
        attempt="known",
        sequence=1,
        status="Accepted",
        policy_context="restricted",
    )
    assert not hasattr(result, "id_tag")
