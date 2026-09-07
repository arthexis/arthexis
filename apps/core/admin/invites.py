from importlib import import_module
import sys

_module = import_module("apps.sites.admin.invites")
sys.modules[__name__] = _module
