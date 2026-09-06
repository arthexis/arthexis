from __future__ import annotations

import sys

from apps.release import changelog as _impl

sys.modules[__name__] = _impl
