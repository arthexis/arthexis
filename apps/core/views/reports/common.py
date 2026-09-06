"""Compatibility alias for report constants now owned by apps.reports."""

import sys

from apps.reports.views import common as _impl

sys.modules[__name__] = _impl
