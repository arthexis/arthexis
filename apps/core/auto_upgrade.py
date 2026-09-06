from __future__ import annotations

import sys

from apps.release import auto_upgrade as _impl

sys.modules[__name__] = _impl
