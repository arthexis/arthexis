from importlib import import_module
import sys

_module = import_module("apps.analytics.views")
sys.modules[__name__] = _module
