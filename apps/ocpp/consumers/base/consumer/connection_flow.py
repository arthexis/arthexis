"""Connection admission flow helpers for charge-point websocket sessions.

This module encapsulates admission checks used before a websocket session is
accepted. It documents the OCPP 1.6 and 2.x boundary as follows:

* subprotocol negotiation stays in transport connection mixins,
* feature-gated charger admission (known vs unknown chargers) is shared across
  OCPP versions and centralized here.

Public extension point:
    ``ConnectionAdmissionService`` can be injected with a custom
    ``feature_state_resolver`` to unit-test or replace policy behavior without
    websocket bootstrapping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature

from ....models import Charger

if TYPE_CHECKING:
    from . import CSMSConsumer

CHARGER_CREATION_FEATURE_SLUG = "standard-charge-point"
CHARGE_POINT_FEATURE_SLUG = "charge-points"


@dataclass(slots=True)
class AdmissionDecision:
    """Result of evaluating charge-point connection admission policy."""

    allowed: bool
    reason: str | None = None


class ConnectionAdmissionService:
    """Service for evaluating if a charge point connection should be admitted."""

    def __init__(
        self,
        *,
        feature_state_resolver: Callable[[Charger | None], AdmissionDecision] | None = None,
        db_call: Callable[[Callable[[], AdmissionDecision]], Callable[[], Awaitable[AdmissionDecision]]] | None = None,
    ) -> None:
        self._feature_state_resolver = feature_state_resolver or self._resolve_feature_state
        self._db_call = db_call

    async def allow_charge_point_connection(self, _consumer: "CSMSConsumer", existing_charger: Charger | None) -> bool:
        """Return whether ``existing_charger`` may connect to the current node."""

        if self._db_call is not None:
            decision = await self._db_call(lambda: self._feature_state_resolver(existing_charger))()
        else:
            decision = self._feature_state_resolver(existing_charger)
        return decision.allowed

    def _resolve_feature_state(self, existing_charger: Charger | None) -> AdmissionDecision:
        """Evaluate node + feature toggles for charger connection admission."""

        node = Node.get_local()
        if not node:
            return AdmissionDecision(True, "node-missing")

        feature = (
            Feature.objects.select_related("node_feature")
            .filter(slug=CHARGER_CREATION_FEATURE_SLUG)
            .first()
        )
        node_feature = feature.node_feature if feature else None
        if not node_feature:
            node_feature = NodeFeature.objects.filter(slug=CHARGE_POINT_FEATURE_SLUG).first()

        if node_feature and not node_feature.is_enabled:
            return AdmissionDecision(False, "node-feature-disabled")

        if feature and not feature.is_enabled:
            if existing_charger:
                return AdmissionDecision(True, "creation-disabled-known")
            return AdmissionDecision(False, "creation-disabled-unknown")

        return AdmissionDecision(True)
