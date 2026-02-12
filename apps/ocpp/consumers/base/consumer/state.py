"""Backward-compatible import surface for consumer session state.

Use ``apps.ocpp.consumers.base.consumer.session_state`` for new code.
"""

from .session_state import ConsumerSessionState as ConsumerState

__all__ = ["ConsumerState"]
