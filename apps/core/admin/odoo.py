"""Compatibility alias for Odoo admin implementation now owned by apps.odoo."""

import sys

from apps.odoo import admin_core as _impl

sys.modules[__name__] = _impl
