"""Compatibility alias for release publish views now owned by apps.release."""

import sys

from apps.release import views as _impl

sys.modules[__name__] = _impl
