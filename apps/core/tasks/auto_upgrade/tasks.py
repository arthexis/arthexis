from __future__ import annotations

import sys

from apps.release.tasks.auto_upgrade import tasks as _impl

sys.modules[__name__] = _impl
