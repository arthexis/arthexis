import sys
from importlib import import_module

_module = import_module("apps.sites.models.lead_base")
sys.modules[__name__] = _module
