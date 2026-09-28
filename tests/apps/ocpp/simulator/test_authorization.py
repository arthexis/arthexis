import asyncio
import pytest

from apps.ocpp.simulator.authorization import (
    AuthorizationAttempt,
    AuthorizationAttemptResult,
    AuthorizationScenario,
    authorization_policy_scenario,
    run_live_authorization_scenario,
)
from apps.ocpp.simulator.network import LiveSimulatorError


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


class FakeAuthorizationTransport:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.received = []

    async def authorize(self, id_tag):
        self.received.append(id_tag)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_live_runner_preserves_order_repeats_and_actual_statuses():
    async def exercise():
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
        transport = FakeAuthorizationTransport(
            ["Accepted", "Invalid", "Accepted"]
        )

        results = await run_live_authorization_scenario(transport, scenario)

        assert transport.received == ["KNOWN", "UNKNOWN", "KNOWN"]
        assert [(r.sequence, r.attempt, r.status, r.repeat_of) for r in results] == [
            (1, "known", "Accepted", None),
            (2, "unknown", "Invalid", None),
            (3, "known-repeat", "Accepted", "known"),
        ]
        assert all(r.policy_context == "restricted" for r in results)

    asyncio.run(exercise())


def test_live_runner_keeps_per_attempt_transport_failures_structured():
    async def exercise():
        scenario = AuthorizationScenario(
            name="mixed",
            attempts=(
                AuthorizationAttempt(name="first", id_tag="ONE"),
                AuthorizationAttempt(name="second", id_tag="TWO"),
                AuthorizationAttempt(name="third", id_tag="THREE"),
            ),
        )
        transport = FakeAuthorizationTransport(
            [
                "Accepted",
                LiveSimulatorError("connection receive failed"),
                "Blocked",
            ]
        )

        results = await run_live_authorization_scenario(transport, scenario)

        assert [r.status for r in results] == ["Accepted", None, "Blocked"]
        assert results[1].error == "connection receive failed"
        assert [r.attempt for r in results] == ["first", "second", "third"]

    asyncio.run(exercise())


@pytest.mark.parametrize("policy_context", ["open", "restricted"])
def test_standard_policy_matrix_covers_roles_without_predicting_status(policy_context):
    scenario = authorization_policy_scenario(
        policy_context=policy_context,
        known_authorized="KNOWN-OK",
        known_denied="KNOWN-NO",
        unknown="UNKNOWN",
    )

    assert scenario.policy_context == policy_context
    assert [(attempt.name, attempt.repeat_of) for attempt in scenario.attempts] == [
        ("known-authorized", None),
        ("known-denied", None),
        ("unknown", None),
        ("known-authorized-repeat", "known-authorized"),
    ]
    assert not any(hasattr(attempt, "expected_status") for attempt in scenario.attempts)


def test_standard_policy_matrix_rejects_unknown_policy_context():
    with pytest.raises(ValueError, match="open or restricted"):
        authorization_policy_scenario(
            policy_context="custom",
            known_authorized="KNOWN-OK",
            known_denied="KNOWN-NO",
            unknown="UNKNOWN",
        )
