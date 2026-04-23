"""Public pages chat settings."""

import os

from utils.env import env_bool

PAGES_CHAT_NOTIFY_STAFF = env_bool("PAGES_CHAT_NOTIFY_STAFF", False)
try:
    PAGES_CHAT_IDLE_ESCALATE_SECONDS = int(
        os.environ.get("PAGES_CHAT_IDLE_ESCALATE_SECONDS", "300")
    )
except (TypeError, ValueError):
    PAGES_CHAT_IDLE_ESCALATE_SECONDS = 300
