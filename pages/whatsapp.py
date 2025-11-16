"""Utilities for forwarding visitor chat messages to WhatsApp."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

from .models import WhatsAppChatBridge

if TYPE_CHECKING:  # pragma: no cover - type checking helpers
    from .models import ChatMessage, ChatSession


logger = logging.getLogger(__name__)


def forward_chat_message(session: "ChatSession", message: "ChatMessage") -> bool:
    """Forward a chat ``message`` to the configured WhatsApp bridge, if available."""

    if not getattr(settings, "PAGES_WHATSAPP_ENABLED", False):
        return False
    if session is None or message is None:
        return False
    number = (getattr(session, "whatsapp_number", "") or "").strip()
    if not number:
        return False
    content = (message.body or "").strip()
    if not content:
        return False
    bridge = WhatsAppChatBridge.objects.for_site(getattr(session, "site", None))
    if bridge is None:
        return False
    try:
        return bridge.send_message(
            recipient=number, content=content, session=session, message=message
        )
    except Exception:  # pragma: no cover - unexpected bridge errors logged for diagnosis
        logger.exception(
            "Unexpected failure forwarding chat message %s for session %s to WhatsApp",
            getattr(message, "pk", None),
            getattr(session, "pk", None),
        )
        return False
