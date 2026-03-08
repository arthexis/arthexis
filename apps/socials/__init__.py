"""Backward-compatible import shim for ``apps.socials``."""

from importlib import import_module
import sys

from apps.comms.socials import *  # noqa: F403

sys.modules.setdefault(__name__ + ".admin", import_module("apps.comms.socials.admin"))
sys.modules.setdefault(__name__ + ".apps", import_module("apps.comms.socials.apps"))
sys.modules.setdefault(__name__ + ".manifest", import_module("apps.comms.socials.manifest"))
sys.modules.setdefault(__name__ + ".models", import_module("apps.comms.socials.models"))
