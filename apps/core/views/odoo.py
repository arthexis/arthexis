"""Compatibility alias for Odoo views now owned by apps.odoo."""

import sys

from apps.odoo import core_views as _impl

sys.modules[__name__] = _impl
