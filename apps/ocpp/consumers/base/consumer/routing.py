"""Backward-compatible import surface for action routing.

Use ``apps.ocpp.consumers.base.consumer.action_dispatch`` for new code.
"""

from .action_dispatch import ActionDispatchRegistry as ActionRouter, build_action_registry

__all__ = ["ActionRouter", "build_action_registry"]
