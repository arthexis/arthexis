"""Backward-compatible import shim for ``apps.chats``."""

from importlib import import_module
import sys

from apps.comms.chats import *  # noqa: F403

sys.modules.setdefault(__name__ + ".admin", import_module("apps.comms.chats.admin"))
sys.modules.setdefault(__name__ + ".apps", import_module("apps.comms.chats.apps"))
sys.modules.setdefault(__name__ + ".manifest", import_module("apps.comms.chats.manifest"))
sys.modules.setdefault(__name__ + ".models", import_module("apps.comms.chats.models"))
