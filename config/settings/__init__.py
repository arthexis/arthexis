"""Assembled Django settings package."""

from .base import *  # noqa: F401,F403
from .security import *  # noqa: F401,F403
from .apps import *  # noqa: F401,F403

# Usage analytics was historically core-owned and therefore present on every node.
# Keep that runtime invariant while ownership moves to its dedicated app.
if "apps.analytics" not in PROJECT_LOCAL_APPS:
    PROJECT_LOCAL_APPS.append("apps.analytics")
if "apps.analytics" not in INSTALLED_APPS:
    INSTALLED_APPS.append("apps.analytics")

# AdminNotice was historically core-owned and therefore available on every node.
# Keep ops universal for this transition while AdminNotice ownership lives there.
if "apps.ops" not in PROJECT_LOCAL_APPS:
    PROJECT_LOCAL_APPS.append("apps.ops")
if "apps.ops" not in INSTALLED_APPS:
    INSTALLED_APPS.append("apps.ops")

from .routing import *  # noqa: F401,F403
from .extensions import *  # noqa: F401,F403
from .middleware import *  # noqa: F401,F403
from .database import *  # noqa: F401,F403
from .i18n import *  # noqa: F401,F403
from .static import *  # noqa: F401,F403
from .logging import *  # noqa: F401,F403
from .celery import *  # noqa: F401,F403

from config.roles import validate_role_settings

validate_role_settings(globals())
