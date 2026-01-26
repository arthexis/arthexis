from __future__ import annotations

from . import auto_upgrade as _auto_upgrade
from . import heartbeat as _heartbeat
from . import release_checks as _release_checks
from . import system_health as _system_health
from . import utils as _utils


def _export_module(module) -> None:
    for name, value in module.__dict__.items():
        if name.startswith("__"):
            continue
        globals()[name] = value


for _module in (
    _heartbeat,
    _release_checks,
    _system_health,
    _utils,
    _auto_upgrade,
):
    _export_module(_module)


del _module
