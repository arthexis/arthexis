"""Compatibility shim for the relocated core app."""
from importlib import import_module
import sys

module = import_module("apps.core")
sys.modules[__name__] = module
