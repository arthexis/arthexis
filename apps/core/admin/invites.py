import sys
from importlib import import_module

_module = import_module("apps.sites.admin.invites")
sys.modules[__name__] = _module
