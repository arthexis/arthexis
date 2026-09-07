"""Compatibility alias for RFID admin forms now owned by apps.cards."""

import sys

from apps.cards import rfid_forms as _impl

sys.modules[__name__] = _impl
