import sys
from importlib import import_module

_module = import_module("apps.analytics.models")
sys.modules[__name__] = _module
