from importlib import import_module
import sys

_module = import_module("apps.analytics.admin")
sys.modules[__name__] = _module
