"""Compatibility alias for RFID authentication views now owned by apps.cards."""

import sys

from apps.cards import auth_views as _impl

sys.modules[__name__] = _impl
