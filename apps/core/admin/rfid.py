"""Compatibility alias for RFID admin implementation now owned by apps.cards."""

import sys

from apps.cards import admin_rfid as _impl

sys.modules[__name__] = _impl
