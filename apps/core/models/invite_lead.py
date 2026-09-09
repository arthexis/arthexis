import sys
from importlib import import_module

_module = import_module("apps.sites.models.invite_lead")
sys.modules[__name__] = _module
