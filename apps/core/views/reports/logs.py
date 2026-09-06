"""Compatibility alias for report log helpers now owned by apps.reports."""

import sys

from apps.reports.views import logs as _impl

sys.modules[__name__] = _impl
