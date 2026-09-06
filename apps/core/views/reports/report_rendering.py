"""Compatibility alias for report rendering helpers now owned by apps.reports."""

import sys

from apps.reports.views import report_rendering as _impl

sys.modules[__name__] = _impl
