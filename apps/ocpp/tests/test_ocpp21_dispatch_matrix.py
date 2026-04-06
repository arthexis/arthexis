from __future__ import annotations

from itertools import product

import pytest

from apps.ocpp.call_error_handlers.dispatch import CALL_ERROR_HANDLERS
from apps.ocpp.call_result_handlers.registry import CALL_RESULT_HANDLER_REGISTRY
from apps.ocpp.views.actions import (
    certificates,
    charging_profiles,
    configuration,
    core,
    display_messages,
    monitoring,
    network_profiles,
    reservations,
)
from apps.protocols.models import ProtocolCall as ProtocolCallModel
from apps.protocols.registry import get_registered_calls

ACTION_MODULES = (
    core,
    configuration,
    charging_profiles,
    display_messages,
    certificates,
    monitoring,
    network_profiles,
    reservations,
)


def _collect_protocol_actions(slug: str) -> dict[str, set[object]]:
    actions: dict[str, set[object]] = {}
    for module in ACTION_MODULES:
        for candidate in module.__dict__.values():
            protocol_calls = getattr(candidate, "__protocol_calls__", set())
            if not protocol_calls:
                continue
            for protocol_slug, direction, action in protocol_calls:
                if protocol_slug != slug or direction != ProtocolCallModel.CSMS_TO_CP:
                    continue
                actions.setdefault(action, set()).add(candidate)
    return actions


@pytest.mark.parametrize(
    "action",
    sorted(_collect_protocol_actions("ocpp201")),
)
def test_listed_ocpp201_actions_have_ocpp21_decorator_parity(action: str) -> None:
    ocpp201_actions = _collect_protocol_actions("ocpp201")
    ocpp21_actions = _collect_protocol_actions("ocpp21")

    assert action in ocpp21_actions
    assert ocpp201_actions[action].issubset(ocpp21_actions[action])


@pytest.mark.parametrize(
    ("protocol_slug", "action"),
    sorted(product(("ocpp201", "ocpp21"), _collect_protocol_actions("ocpp201"))),
)
def test_protocol_action_matrix_routes_to_real_handlers(
    protocol_slug: str,
    action: str,
) -> None:
    protocol_handlers = get_registered_calls(protocol_slug, ProtocolCallModel.CSMS_TO_CP).get(
        action,
        set(),
    )

    assert protocol_handlers
    assert any(
        handler.__module__.startswith("apps.ocpp.views.actions.")
        for handler in protocol_handlers
    )

    result_handler = CALL_RESULT_HANDLER_REGISTRY.get(action)
    error_handler = CALL_ERROR_HANDLERS.get(action)

    assert result_handler is not None
    assert result_handler.__module__.startswith("apps.ocpp.call_result_handlers.")

    assert error_handler is not None
    assert error_handler.__module__.startswith("apps.ocpp.call_error_handlers.")
