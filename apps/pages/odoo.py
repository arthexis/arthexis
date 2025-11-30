"""Utilities for forwarding visitor chat messages to Odoo."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models import OdooChatBridge

if TYPE_CHECKING:  # pragma: no cover - type checking helpers
    from .models import ChatMessage, ChatSession


logger = logging.getLogger(__name__)


def forward_chat_message(session: "ChatSession", message: "ChatMessage") -> bool:
    """Forward a chat ``message`` to the configured Odoo bridge, if available."""

    if session is None or message is None:
        return False
    content = (message.body or "").strip()
    if not content:
        return False
    bridge = OdooChatBridge.objects.for_site(getattr(session, "site", None))
    if bridge is None:
        return False
    try:
        return bridge.post_message(session, message)
    except Exception:  # pragma: no cover - unexpected bridge errors logged for diagnosis
        logger.exception(
            "Unexpected failure forwarding chat message %s for session %s to Odoo",
            getattr(message, "pk", None),
            getattr(session, "pk", None),
        )
        return False
