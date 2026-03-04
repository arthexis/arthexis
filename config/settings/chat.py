"""Public pages chat settings."""

import os

from utils.env import env_bool

PAGES_CHAT_ENABLED = env_bool("PAGES_CHAT_ENABLED", True)
PAGES_CHAT_NOTIFY_STAFF = env_bool("PAGES_CHAT_NOTIFY_STAFF", False)
try:
    PAGES_CHAT_IDLE_ESCALATE_SECONDS = int(
        os.environ.get("PAGES_CHAT_IDLE_ESCALATE_SECONDS", "300")
    )
except (TypeError, ValueError):
    PAGES_CHAT_IDLE_ESCALATE_SECONDS = 300
PAGES_CHAT_SOCKET_PATH = os.environ.get("PAGES_CHAT_SOCKET_PATH", "/ws/pages/chat/")
