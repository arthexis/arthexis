"""Compatibility shim for the relocated app app."""
from importlib import import_module
import sys

module = import_module("apps.app")
sys.modules[__name__] = module
