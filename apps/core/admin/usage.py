import sys
from importlib import import_module

_module = import_module("apps.analytics.admin")
sys.modules[__name__] = _module
