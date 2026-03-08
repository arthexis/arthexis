"""Backward-compatible import shim for ``apps.teams``."""

from importlib import import_module
import sys

from apps.comms.teams import *  # noqa: F403

sys.modules.setdefault(__name__ + ".admin", import_module("apps.comms.teams.admin"))
sys.modules.setdefault(__name__ + ".apps", import_module("apps.comms.teams.apps"))
sys.modules.setdefault(__name__ + ".forms", import_module("apps.comms.teams.forms"))
sys.modules.setdefault(__name__ + ".manifest", import_module("apps.comms.teams.manifest"))
sys.modules.setdefault(__name__ + ".models", import_module("apps.comms.teams.models"))
sys.modules.setdefault(__name__ + ".routes", import_module("apps.comms.teams.routes"))
sys.modules.setdefault(__name__ + ".urls", import_module("apps.comms.teams.urls"))
sys.modules.setdefault(__name__ + ".views", import_module("apps.comms.teams.views"))
