"""Compatibility alias for user admin implementation now owned by apps.users."""

import sys

from apps.users import admin_core as _impl

sys.modules[__name__] = _impl
