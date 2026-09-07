"""Compatibility alias for RFID/card views now owned by apps.cards."""

import sys

from apps.cards import core_views as _impl

sys.modules[__name__] = _impl
