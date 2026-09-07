"""Compatibility alias for Odoo quote-report services now owned by apps.odoo."""

import sys

from apps.odoo import quote_report as _impl

sys.modules[__name__] = _impl
