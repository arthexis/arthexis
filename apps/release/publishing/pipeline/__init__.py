"""Release publishing pipeline package.

Compatibility entrypoint for existing imports from ``apps.release.publishing.pipeline``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import actions as _actions


class _PipelineModule(ModuleType):
    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        if not name.startswith("__") and hasattr(_actions, name):
            setattr(_actions, name, value)


for _name in dir(_actions):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_actions, _name)

__all__ = sorted(_name for _name in dir(_actions) if not _name.startswith("__"))
sys.modules[__name__].__class__ = _PipelineModule
