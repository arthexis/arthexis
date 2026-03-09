"""Action routing helpers for inbound OCPP Call messages.

This module centralizes the action-to-handler registry used for OCPP 1.6 and
OCPP 2.x call dispatch. Routing itself does not perform DB writes; side effects
occur only inside delegated handlers on the consumer.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from apps.ocpp.consumers.csms.dispatch import build_action_registry

if TYPE_CHECKING:
    from . import CSMSConsumer


Handler = Callable[[dict[str, Any], str, str | None, str | None], Awaitable[dict]]


class ActionRouter:
    """Explicit action registry for :class:`CSMSConsumer`.

    The router assumes OCPP Call frames are already validated and normalized by
    the websocket dispatch layer. It returns bound coroutine handlers on the
    consumer, where DB side effects (transactions, meter persistence, etc.)
    occur.
    """

    def __init__(self, consumer: "CSMSConsumer") -> None:
        self.consumer = consumer
        self._handlers: dict[str, Handler] = self._build_registry()

    def _build_registry(self) -> dict[str, Handler]:
        return build_action_registry(self.consumer)

    def resolve(self, action: str) -> Handler | None:
        """Return the bound handler for the OCPP action name."""

        return self._handlers.get(action)
